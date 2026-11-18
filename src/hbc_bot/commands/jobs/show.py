"""Show one job."""
from __future__ import annotations

import discord

from ...ui import embeds
from .._common import respond


def option_help() -> dict[str, str]:
    """Slash option descriptions for /jobs show."""
    return {"job_id": "Numeric job id from /jobs list"}


async def main(bot, interaction: discord.Interaction, job_id: int) -> None:
    """Show one job embed by numeric ID."""
    ephemeral = bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id)
    job = bot.jobs.get(job_id)
    if job is None:
        await respond(interaction, f"No job #{job_id}.", ephemeral=ephemeral)
        return
    await respond(
        interaction,
        ephemeral=ephemeral,
        embed=embeds.job_embed(job, bot.cfg.show_log_tail),
    )
