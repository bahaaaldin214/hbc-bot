"""Configuration from environment variables, with an optional .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ. Real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # Strip matching single/double quotes around the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _ids(name: str) -> frozenset[int]:
    """Parse a comma-separated env var into Discord snowflake IDs."""
    return frozenset(int(p) for p in os.environ.get(name, "").replace(" ", "").split(",") if p)


@dataclass(frozen=True)
class Config:
    """Runtime settings for the Discord bot (token, trusted guild, DM allowlist, paths)."""

    token: str
    guild_id: int | None
    public_channel_ids: frozenset[int]
    allow_dm: bool
    dm_user_ids: frozenset[int]
    host: str
    tools_dir: Path
    launcher: Path
    bash: str
    data_dir: Path
    job_timeout_s: float | None
    show_log_tail: bool
    embed_edit_interval_s: float
    max_log_upload_bytes: int
    max_upload_bytes: int
    confirm_timeout_s: float

    @property
    def db_path(self) -> Path:
        """SQLite path for job history."""
        return self.data_dir / "jobs.sqlite3"

    @property
    def log_dir(self) -> Path:
        """Directory for per-job stdout log files."""
        return self.data_dir / "logs"

    @property
    def upload_dir(self) -> Path:
        """Staging area for Discord file uploads (``file`` args)."""
        return self.data_dir / "uploads"

    def ephemeral_for(self, channel_id: int | None, guild_id: int | None = None) -> bool:
        """Return True if slash replies should be Discord-ephemeral.

        False → reply stays in history (public channels + DMs).
        Ephemeral replies are invoker-only and never become normal channel messages.
        """
        if guild_id is None:
            return False
        return not (channel_id is not None and channel_id in self.public_channel_ids)

    @classmethod
    def from_env(cls) -> "Config":
        """Build config from process env and optional ``.env`` file."""
        load_env_file(Path(os.environ.get("HBC_BOT_ENV", ".env")))
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("DISCORD_TOKEN is empty. Paste the bot token into .env (see .env.example).")
        # vosslink default; override with BAHAA_TOOLS_DIR on Windows (Z:\\Users\\bahaa\\tools).
        tools_dir = Path(
            os.environ.get("BAHAA_TOOLS_DIR") or "/mnt/nfs/lss/vosslabhpc/Users/bahaa/tools"
        )
        guild = os.environ.get("HBC_GUILD_ID", "").strip()
        timeout = float(os.environ.get("HBC_JOB_TIMEOUT_S") or 86400)
        allow_dm = (os.environ.get("HBC_ALLOW_DM") or "1") not in ("0", "false", "no")
        return cls(
            token=token,
            guild_id=int(guild) if guild else None,
            public_channel_ids=_ids("HBC_PUBLIC_CHANNEL_IDS"),
            allow_dm=allow_dm,
            dm_user_ids=_ids("HBC_DM_USER_IDS"),
            # Must equal a tool's config.toml `[run] host`, or the bot refuses to start it.
            host=(os.environ.get("HBC_HOST") or "vosslink").strip().lower(),
            tools_dir=tools_dir,
            launcher=Path(os.environ.get("BAHAA_LAUNCHER") or tools_dir / "bahaa"),
            bash=os.environ.get("HBC_BASH") or "bash",
            data_dir=Path(os.environ.get("HBC_BOT_DATA_DIR") or "data"),
            # 0 in env means "never time out".
            job_timeout_s=timeout if timeout > 0 else None,
            show_log_tail=(os.environ.get("HBC_SHOW_LOG_TAIL") or "1") not in ("0", "false", "no"),
            embed_edit_interval_s=float(os.environ.get("HBC_EMBED_EDIT_INTERVAL_S") or 2.0),
            max_log_upload_bytes=int(os.environ.get("HBC_MAX_LOG_UPLOAD_BYTES") or (8 * 1024 * 1024)),
            max_upload_bytes=int(os.environ.get("HBC_MAX_UPLOAD_BYTES") or (8 * 1024 * 1024)),
            confirm_timeout_s=float(os.environ.get("HBC_CONFIRM_TIMEOUT_S") or 300),
        )
