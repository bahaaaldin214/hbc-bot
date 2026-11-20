"""Rescan the bahaa tools folder."""
from __future__ import annotations

import asyncio

import discord

from ._common import deny, visible_tools


def default_permissions() -> discord.Permissions:
    """Admins only until the server grants roles/channels (Server Settings → Integrations)."""
    return discord.Permissions.none()


async def main(bot, interaction: discord.Interaction) -> None:
    """Rescan BAHAA_TOOLS_DIR (tools, config.toml, args.toml) and report what is available."""
    if not bot.policy.allowed(interaction):
        await deny(interaction, bot.policy.denial_message(interaction))
        return
    # The scan reads a network share and can outlast Discord's 3 s reply window: ack first.
    ephemeral = bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id)
    await interaction.response.defer(ephemeral=ephemeral, thinking=True)
    try:
        total = await asyncio.to_thread(bot.registry.scan)
    except OSError as error:
        await interaction.followup.send(
            f"Could not read the tools folder ({type(error).__name__}).", ephemeral=True
        )
        return
    available = visible_tools(bot, interaction)
    runnable = sum(len(spec.runnable_commands()) for spec in available.values())
    text = f"Scanned {total} tools; {len(available)} available here ({runnable} runnable commands)."
    await interaction.followup.send(text, ephemeral=ephemeral)
    problems = bot.registry.problems()
    if problems:
        # Broken toml names lab file paths: keep that private (second, ephemeral follow-up).
        lines = "\n".join(f"- {problem}" for problem in problems)
        await interaction.followup.send(
            f"Broken toml (those tools/commands stay off):\n{lines}"[:2000], ephemeral=True
        )
