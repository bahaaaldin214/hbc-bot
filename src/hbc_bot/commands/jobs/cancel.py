"""Cancel a running or queued job."""
from __future__ import annotations

import discord

from .._common import respond


def option_help() -> dict[str, str]:
    """Slash option descriptions for /jobs cancel."""
    return {"job_id": "Numeric job id to cancel"}


async def main(bot, interaction: discord.Interaction, job_id: int) -> None:
    """Request cancel for a running/queued job ID."""
    ok = bot.jobs.cancel(job_id)
    msg = f"Cancelling job #{job_id}…" if ok else f"Job #{job_id} is not running."
    await respond(
        interaction,
        msg,
        ephemeral=bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id),
    )
