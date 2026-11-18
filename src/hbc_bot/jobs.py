"""Job model, execution and SQLite history. No Discord imports here."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import progress, runner


class JobStatus(str, Enum):
    """Lifecycle states stored in the jobs.status column."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"

    def is_final(self) -> bool:
        """True when the job will not change status again."""
        return self in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
            JobStatus.INTERRUPTED,
        }


def _schema_path() -> Path:
    """Path to the SQL schema file shipped next to this package."""
    return Path(__file__).resolve().parent / "sql" / "jobs_schema.sql"


def _apply_schema(db: sqlite3.Connection) -> None:
    """Execute the jobs schema SQL file against ``db``."""
    sql = _schema_path().read_text(encoding="utf-8")
    db.executescript(sql)


def _job_from_row(row: sqlite3.Row) -> Job:
    """Build a Job dataclass from a sqlite3.Row (SELECT *)."""
    return Job(
        id=row["id"],
        tool=row["tool"],
        command=row["command"],
        args=row["args"],
        user_id=row["user_id"],
        user_name=row["user_name"],
        status=row["status"],
        returncode=row["returncode"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        log_path=row["log_path"],
    )


@dataclass
class Job:
    """One tool run: persisted fields plus live progress/tail for Discord embeds."""

    id: int
    tool: str
    command: str
    args: str
    user_id: int
    user_name: str
    status: str = JobStatus.QUEUED.value
    returncode: int | None = None
    created_at: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None
    log_path: str | None = None
    # live-only state (not persisted)
    progress: progress.Progress | None = None
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=12))
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def finished(self) -> bool:
        """True when the job will not change status again."""
        try:
            return JobStatus(self.status).is_final()
        except ValueError:
            return False

    @property
    def title(self) -> str:
        """Short command line for embeds and logs."""
        return f"bahaa {self.tool} {self.command} {self.args}".rstrip()


class JobManager:
    """Queue/run jobs, persist history, expose cancel/wait for Discord cogs."""

    def __init__(self, db_path: Path, log_dir: Path, timeout: float | None = None):
        """Open SQLite, apply schema, mark stale rows interrupted, prepare runtime maps."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        self.timeout = timeout
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        _apply_schema(self.db)
        # Anything still marked active belongs to a previous bot process.
        self.db.execute(
            "UPDATE jobs SET status=?, ended_at=? WHERE status IN (?, ?)",
            (
                JobStatus.INTERRUPTED.value,
                time.time(),
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
            ),
        )
        self.db.commit()
        self.active: dict[int, Job] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        # One lock per tool name → same tool never overlaps itself.
        self._locks: dict[str, asyncio.Lock] = {}

    def _save(self, job: Job) -> None:
        """Persist mutable job fields to SQLite."""
        self.db.execute(
            "UPDATE jobs SET status=?, returncode=?, started_at=?, ended_at=?, log_path=? WHERE id=?",
            (job.status, job.returncode, job.started_at, job.ended_at, job.log_path, job.id),
        )
        self.db.commit()

    def get(self, job_id: int) -> Job | None:
        """Return a live job if active, else a row from SQLite, else None."""
        if job_id in self.active:
            return self.active[job_id]
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_from_row(row) if row else None

    def recent(self, limit: int = 10) -> list[Job]:
        """Newest jobs, preferring in-memory objects when still active."""
        rows = self.db.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.active.get(r["id"]) or _job_from_row(r) for r in rows]

    def submit(self, tool: str, command: str, args: str, argv: list[str], user_id: int, user_name: str) -> Job:
        """Record a job and start it. Jobs of the same tool run one at a time."""
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO jobs (tool, command, args, user_id, user_name, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (tool, command, args, user_id, user_name, JobStatus.QUEUED.value, now),
        )
        self.db.commit()
        job = Job(cur.lastrowid, tool, command, args, user_id, user_name, created_at=now)
        job.log_path = str(self.log_dir / f"job-{job.id}.log")
        self.active[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job, argv), name=f"job-{job.id}")
        return job

    def cancel(self, job_id: int) -> bool:
        """Request cancellation of a running/queued task. False if already done."""
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def wait(self, job_id: int) -> None:
        """Await a job task (used by tests). Ignores missing IDs."""
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def _on_line(self, job: Job, line: str) -> None:
        """Update progress or log tail from one stdout line; signal embed refresh."""
        p = progress.parse(line)
        if p is not None:
            job.progress = p
        elif line.strip():
            job.tail.append(line)
        job.changed.set()

    async def _run(self, job: Job, argv: list[str]) -> None:
        """Execute argv under the per-tool lock and finalize job status."""
        lock = self._locks.setdefault(job.tool, asyncio.Lock())
        try:
            async with lock:
                job.status, job.started_at = JobStatus.RUNNING.value, time.time()
                self._save(job)
                job.changed.set()
                job.returncode = await runner.run(
                    argv, Path(job.log_path), lambda line: self._on_line(job, line), self.timeout
                )
                job.status = (
                    JobStatus.SUCCEEDED.value if job.returncode == 0 else JobStatus.FAILED.value
                )
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED.value
        except asyncio.TimeoutError:
            job.status = JobStatus.TIMEOUT.value
        except Exception as exc:  # e.g. launcher not found
            job.status = JobStatus.FAILED.value
            job.tail.append(f"{type(exc).__name__}: {exc}")
        finally:
            job.ended_at = time.time()
            self._save(job)
            self.active.pop(job.id, None)
            self._tasks.pop(job.id, None)
            job.changed.set()

    async def shutdown(self) -> None:
        """Cancel active jobs and close the database (bot shutdown)."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.db.close()
