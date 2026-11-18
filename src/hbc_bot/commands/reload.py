"""Rescan the bahaa tools folder."""
from __future__ import annotations

import asyncio

import discord

from ._common import deny, respond, visible_tools


def default_permissions() -> discord.Permissions:
    """Admins only until the server grants roles/channels (Server Settings → Integrations)."""
    return discord.Permissions.none()


async def main(bot, interaction: discord.Interaction) -> None:
    """Rescan BAHAA_TOOLS_DIR (tools, config.toml, args.toml) and report what is available."""
    if not bot.policy.allowed(interaction):
        await deny(interaction, bot.policy.denial_message(interaction))
        return
    total = await asyncio.to_thread(bot.registry.scan)
    available = visible_tools(bot, interaction)
    runnable = sum(len(spec.runnable_commands()) for spec in available.values())
    text = f"Scanned {total} tools; {len(available)} available here ({runnable} runnable commands)."
    problems = bot.registry.problems()
    if not problems:
        ephemeral = bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id)
        await respond(interaction, text, ephemeral=ephemeral)
        return
    # Broken toml names lab file paths: keep that private.
    lines = "\n".join(f"- {problem}" for problem in problems)
    await deny(interaction, f"{text}\nBroken toml (those tools/commands stay off):\n{lines}"[:2000])
