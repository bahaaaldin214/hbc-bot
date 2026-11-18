"""Run one subprocess, stream its output line by line, kill the whole tree on cancel/timeout.

Never uses a shell: argv is passed as a list.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate ``proc`` and its children (process group / taskkill)."""
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            # start_new_session=True put the child in its own group → killpg works.
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
    except ProcessLookupError:
        pass
    try:
        # wait() also drains the output pipe; a grandchild that escaped the kill
        # (seen with Git Bash on Windows) can hold it open, so do not wait forever.
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        pass


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a log line."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


async def run(
    argv: list[str],
    log_path: Path,
    on_line: Callable[[str], None],
    timeout: float | None = None,
) -> int:
    """Run argv, tee output to log_path, call on_line per line. Returns the exit code.

    Raises asyncio.TimeoutError on timeout; on task cancellation the process tree is killed.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    posix = os.name == "posix"
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=1 << 20,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=posix,
        creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    async def pump() -> int:
        """Read stdout lines until EOF, then return the process exit code."""
        assert proc.stdout is not None
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            while True:
                try:
                    raw = await proc.stdout.readline()
                except ValueError:  # line longer than the buffer limit
                    raw = await proc.stdout.read(1 << 20)
                if not raw:
                    break
                line = _strip_ansi(raw.decode("utf-8", "replace")).rstrip("\r\n")
                # Keep the last state of \\r progress lines (carriage-return redraws).
                line = line.rsplit("\r", 1)[-1]
                log.write(line + "\n")
                log.flush()
                on_line(line)
        return await proc.wait()

    try:
        return await asyncio.wait_for(pump(), timeout)
    finally:
        # shield so cancellation of the outer task still finishes the kill.
        await asyncio.shield(_kill_tree(proc))


if __name__ == "__main__":  # manual check: python -m hbc_bot.runner <cmd> [args...]
    code = asyncio.run(run(sys.argv[1:], Path("runner-test.log"), print))
    print(f"exit {code}")
