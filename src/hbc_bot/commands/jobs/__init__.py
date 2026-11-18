"""Job history and control."""
from __future__ import annotations

import discord

from .._common import deny


def default_permissions() -> discord.Permissions:
    """Admins only until the server grants roles/channels (Server Settings → Integrations)."""
    return discord.Permissions.none()


async def check(bot, interaction: discord.Interaction) -> bool:
    """Gate all /jobs subcommands behind the bot-side policy (trusted guild / DM allowlist)."""
    if bot.policy.allowed(interaction):
        return True
    await deny(interaction, bot.policy.denial_message(interaction))
    return False
