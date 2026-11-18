"""Registry + job execution against a fake tools tree (no Discord needed)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from hbc_bot import progress
from hbc_bot.jobs import JobManager, JobStatus
from hbc_bot.registry import Registry


def _bash() -> str | None:
    """Prefer Git Bash on Windows; otherwise PATH bash."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return shutil.which("bash")


def _write(path: Path, text: str) -> None:
    """Write a UTF-8 text file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    """Minimal fake bahaa tools tree for registry/job tests."""
    root = tmp_path / "tools"
    launcher = """#!/usr/bin/env bash
tool="$1"; cmd="$2"; shift 2
exec bash "$(dirname "${BASH_SOURCE[0]}")/$tool/$cmd/run.sh" "$@"
"""
    _write(root / "bahaa", launcher)
    _write(root / "demo" / "tool.sh", "#!/usr/bin/env bash\n")
    _write(root / "demo" / "HELP.md", "# demo\n\nA demo tool.\n")
    _write(root / "demo" / "ok" / "run.sh", 'echo "hello $1"\necho "::progress 40 halfway"\necho done\n')
    _write(root / "demo" / "fail" / "run.sh", "echo boom >&2\nexit 3\n")
    _write(root / "demo" / "slow" / "run.sh", "echo started\nsleep 60\n")
    _write(root / "demo" / "_private" / "run.sh", "echo no\n")
    _write(root / "notatool" / "x" / "run.sh", "echo no\n")
    return root


def test_registry_scan(tools: Path) -> None:
    """Discover demo tool and hide underscored / incomplete trees."""
    reg = Registry(tools)
    assert reg.scan() == 1
    demo = reg.tools["demo"]
    assert demo.summary == "A demo tool."
    assert sorted(demo.commands) == ["fail", "ok", "slow"]
    assert reg.get("demo", "ok") is not None
    assert reg.get("demo", "_private") is None
    assert reg.get("notatool") is None


def test_progress_parse() -> None:
    """Parse ::progress lines and clamp oversize percents."""
    assert progress.parse("::progress 40 halfway") == progress.Progress(40.0, "halfway")
    assert progress.parse("::progress 250").percent == 100.0
    assert progress.parse("progress 40") is None


def _manager(tmp_path: Path) -> JobManager:
    """JobManager pointed at a temp data dir."""
    return JobManager(tmp_path / "data" / "jobs.sqlite3", tmp_path / "data" / "logs")


def _argv(tools: Path, *rest: str) -> list[str]:
    """Build argv for the fake bahaa launcher."""
    bash = _bash()
    assert bash is not None
    return [bash, str(tools / "bahaa"), *rest]


@pytest.mark.skipif(_bash() is None, reason="bash not available")
def test_job_success_and_failure(tools: Path, tmp_path: Path) -> None:
    """Successful and failing jobs persist status, tail, and progress."""

    async def go():
        mgr = _manager(tmp_path)
        ok = mgr.submit("demo", "ok", "world", _argv(tools, "demo", "ok", "world"), 1, "tester")
        await mgr.wait(ok.id)
        bad = mgr.submit("demo", "fail", "", _argv(tools, "demo", "fail"), 1, "tester")
        await mgr.wait(bad.id)
        stored = mgr.get(bad.id)
        await mgr.shutdown()
        return ok, bad, stored

    ok, bad, stored = asyncio.run(go())
    assert ok.status == JobStatus.SUCCEEDED.value and ok.returncode == 0
    assert list(ok.tail) == ["hello world", "done"]
    assert ok.progress == progress.Progress(40.0, "halfway")
    assert Path(ok.log_path).read_text(encoding="utf-8").splitlines()[0] == "hello world"
    assert bad.status == JobStatus.FAILED.value and bad.returncode == 3
    assert list(bad.tail) == ["boom"]
    assert stored.status == JobStatus.FAILED.value and stored.returncode == 3


@pytest.mark.skipif(_bash() is None, reason="bash not available")
def test_job_cancel_kills_process(tools: Path, tmp_path: Path) -> None:
    """Cancel marks a long-running job cancelled."""

    async def go():
        mgr = _manager(tmp_path)
        job = mgr.submit("demo", "slow", "", _argv(tools, "demo", "slow"), 1, "tester")
        await asyncio.wait_for(job.changed.wait(), 20)
        while not job.tail:
            await asyncio.sleep(0.1)
        assert mgr.cancel(job.id)
        await asyncio.wait_for(mgr.wait(job.id), 20)
        await mgr.shutdown()
        return job

    job = asyncio.run(go())
    assert job.status == JobStatus.CANCELLED.value


def test_stale_jobs_marked_interrupted(tmp_path: Path) -> None:
    """Reopening the DB marks leftover running rows interrupted."""

    async def go():
        mgr = _manager(tmp_path)
        mgr.db.execute(
            "INSERT INTO jobs (tool, command, args, user_id, user_name, status, created_at) "
            "VALUES ('t','c','',1,'u','running',0)"
        )
        mgr.db.commit()
        mgr.db.close()
        mgr2 = _manager(tmp_path)
        status = mgr2.recent(1)[0].status
        await mgr2.shutdown()
        return status

    assert asyncio.run(go()) == JobStatus.INTERRUPTED.value


def test_ephemeral_for_public_channels(make_cfg) -> None:
    """Public channels and DMs keep history; other guild channels stay ephemeral."""
    cfg = make_cfg()
    assert cfg.ephemeral_for(111, guild_id=1) is False
    assert cfg.ephemeral_for(222, guild_id=1) is True
    assert cfg.ephemeral_for(None, guild_id=None) is False


def test_registry_surfaces_from_config_toml(tools: Path) -> None:
    """config.toml decides the Discord surface; args.toml decides what is runnable."""
    reg = Registry(tools)
    reg.scan()
    # No config.toml → on no surface (fail closed), and nothing is runnable.
    assert reg.visible(True) == {} and reg.visible(False) == {}
    assert reg.tools["demo"].runnable_commands() == {}

    _write(tools / "demo" / "config.toml", "[discord]\nguild = true\n")
    _write(tools / "demo" / "ok" / "args.toml", "")
    _write(tools / "demo" / "fail" / "args.toml", '[[arg]]\nname = "x"\nkind = "nope"\n')
    reg.scan()
    assert list(reg.visible(True)) == ["demo"] and reg.visible(False) == {}
    demo = reg.tools["demo"]
    assert list(demo.runnable_commands()) == ["ok"]
    assert not demo.commands["fail"].runnable and "kind must be one of" in demo.commands["fail"].schema_error
    assert len(reg.problems()) == 1

    _write(tools / "demo" / "config.toml", "[discord]\nserver = true\n")
    reg.scan()
    assert reg.visible(True) == {} and len(reg.problems()) == 2


def test_schema_sql_exists() -> None:
    """Jobs schema ships as a SQL file next to the package."""
    path = Path(__file__).resolve().parents[1] / "src" / "hbc_bot" / "sql" / "jobs_schema.sql"
    text = path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS jobs" in text


def test_load_commands_from_files() -> None:
    """commands/ files register as slash commands without cog classes."""
    import discord
    from discord.ext import commands

    from hbc_bot.command_loader import load_commands

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    count = load_commands(bot, bot.tree)
    names = {cmd.name for cmd in bot.tree.get_commands()}
    assert count >= 6
    assert {"pipeline", "help", "reload", "jobs"} <= names
    jobs = bot.tree.get_command("jobs")
    assert jobs is not None
    assert {c.name for c in jobs.commands} == {"list", "show", "cancel"}
    # Discord-native gate: runners are admin-only by default, /help stays open.
    for gated in ("pipeline", "reload", "jobs"):
        assert bot.tree.get_command(gated).default_permissions == discord.Permissions.none()
    assert bot.tree.get_command("help").default_permissions is None
    # No free-text args option: values come from the schema-built form only.
    assert [p.name for p in bot.tree.get_command("pipeline").parameters] == ["command"]


def test_commands_registered_for_guild_and_optional_dm(make_cfg) -> None:
    """Contexts are Discord-native: DM context only when HBC_ALLOW_DM, never user-install."""
    from hbc_bot.__main__ import HBCBot

    def payload(allow_dm: bool) -> dict:
        """Serialized /pipeline as it would be synced to Discord."""
        bot = HBCBot(make_cfg(allow_dm=allow_dm))
        from hbc_bot.command_loader import load_commands

        load_commands(bot, bot.tree)
        return bot.tree.get_command("pipeline").to_dict(bot.tree)

    with_dm, without_dm = payload(True), payload(False)
    assert with_dm["default_member_permissions"] == 0
    assert sorted(with_dm["contexts"]) == [0, 1] and without_dm["contexts"] == [0]
    assert with_dm["integration_types"] == [0]
