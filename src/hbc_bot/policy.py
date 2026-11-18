"""Bot-side gates for what Discord itself cannot express.

Who may run a command in the lab server, and in which channels, is Discord's job:
gated commands register with ``default_member_permissions = 0`` (admins only) and
admins grant roles / members / channels under Server Settings → Integrations.

Left for the bot: trust only the lab guild, the DM user allowlist, and each tool's
``config.toml`` (surface + host).
"""
from __future__ import annotations

import discord

from .config import Config
from .jobs import Job
from .registry import ToolSpec


class Policy:
    """Checks applied before any tool runs (slash commands, buttons, future LLM path)."""

    def __init__(self, cfg: Config):
        """Attach config used for the trusted guild and DM allowlist."""
        self.cfg = cfg

    def allowed(self, interaction: discord.Interaction) -> bool:
        """Return True if this context may use the bot at all."""
        # DMs: Discord has no per-user command permissions there. Fail closed if empty.
        if interaction.guild_id is None:
            return self.cfg.allow_dm and interaction.user.id in self.cfg.dm_user_ids
        # Command permissions are per server, so another server's admins must not count.
        return self.cfg.guild_id is not None and interaction.guild_id == self.cfg.guild_id

    def denial_message(self, interaction: discord.Interaction) -> str:
        """User-facing text for a failed ``allowed`` check."""
        if interaction.guild_id is None:
            return "DMs are restricted to an allowlist."
        return "This bot only runs lab tools in its home server (HBC_GUILD_ID)."

    def tool_denial(self, interaction: discord.Interaction, tool: ToolSpec | None) -> str | None:
        """Why ``tool`` may not start here (config.toml surface / host), or None if it may."""
        if tool is None or tool.config is None or not tool.on_surface(interaction.guild_id is not None):
            return "That tool is not available here. Try `/help`."
        if tool.config.host.value != self.cfg.host:
            return f"`{tool.name}` runs on `{tool.config.host.value}`; this bot runs on `{self.cfg.host}`."
        return None

    async def can_cancel(self, interaction: discord.Interaction, job: Job) -> bool:
        """Return True if this user may cancel ``job`` from its embed button.

        Buttons are outside Discord's command permissions, so: the requester, the bot
        owner, or a lab-server member with Manage Server.
        """
        if interaction.user.id == job.user_id:
            return True
        if not self.allowed(interaction):
            return False
        if interaction.guild_id is not None and interaction.permissions.manage_guild:
            return True
        return await interaction.client.is_owner(interaction.user)
