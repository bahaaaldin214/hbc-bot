"""Entry point: python -m hbc_bot"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .command_loader import load_commands
from .config import Config
from .jobs import JobManager
from .policy import Policy
from .registry import Registry

log = logging.getLogger("hbc_bot")


class HBCBot(commands.Bot):
    """Discord bot that exposes bahaa tools via slash commands."""

    def __init__(self, cfg: Config):
        """Wire config, registry, and policy. Slash-only (no message-content intent)."""
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            help_command=None,
            # Discord-native "where": server always, bot DM only when enabled, and
            # never as a user-installed app (commands would follow users elsewhere).
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=cfg.allow_dm, private_channel=False
            ),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        )
        self.cfg = cfg
        self.registry = Registry(cfg.tools_dir)
        self.policy = Policy(cfg)
        self.jobs: JobManager | None = None

    async def setup_hook(self) -> None:
        """Scan tools, load file-based commands, sync slash commands."""
        self.jobs = JobManager(self.cfg.db_path, self.cfg.log_dir, self.cfg.job_timeout_s)
        n = self.registry.scan()
        log.info("found %d tools in %s", n, self.cfg.tools_dir)
        log.info(
            "on Discord: guild=%s dm=%s",
            sorted(self.registry.visible(True)),
            sorted(self.registry.visible(False)),
        )
        for problem in self.registry.problems():
            log.warning("tool toml problem: %s", problem)
        if self.cfg.guild_id is None:
            log.warning("HBC_GUILD_ID is not set: server use is refused, only DMs can work")
        loaded = load_commands(self, self.tree)
        log.info("loaded %d slash commands from commands/", loaded)
        # Global sync serves DMs. Discord can take up to ~1h to drop renamed globals
        # from clients, so also copy onto the lab guild for an instant server update.
        synced = await self.tree.sync()
        log.info(
            "synced %d global app commands: %s",
            len(synced),
            sorted(cmd.name for cmd in synced),
        )
        if self.cfg.guild_id:
            guild = discord.Object(self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            log.info(
                "synced %d guild app commands: %s",
                len(guild_synced),
                sorted(cmd.name for cmd in guild_synced),
            )

    async def on_ready(self) -> None:
        """Log identity once the gateway session is ready."""
        log.info("logged in as %s", self.user)

    async def close(self) -> None:
        """Shut down job manager before closing the Discord client."""
        if self.jobs is not None:
            await self.jobs.shutdown()
            self.jobs = None
        await super().close()


def main() -> None:
    """Load config from env and run the bot until interrupted."""
    cfg = Config.from_env()
    HBCBot(cfg).run(cfg.token, log_level=logging.INFO, root_logger=True)


if __name__ == "__main__":
    main()
