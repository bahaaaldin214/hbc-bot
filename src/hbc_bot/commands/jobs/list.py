"""Show recent jobs."""
from __future__ import annotations

import discord

from ...ui import embeds
from .._common import respond


async def main(bot, interaction: discord.Interaction) -> None:
    """Show the most recent jobs from SQLite / live state."""
    ephemeral = bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id)
    await respond(
        interaction,
        ephemeral=ephemeral,
        embed=embeds.job_list_embed(bot.jobs.recent(10)),
    )
