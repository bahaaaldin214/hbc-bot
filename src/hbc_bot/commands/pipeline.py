"""Run a lab pipeline command on the lab machine."""
from __future__ import annotations

import discord

from ._common import autocomplete_choices, command_error, deny, visible_tools
from ._launch import Launch


def option_help() -> dict[str, str]:
    """Slash option descriptions for /pipeline."""
    return {"command": "Pipeline step (its options open in a form)"}


def default_permissions() -> discord.Permissions:
    """Admins only until the server grants roles/channels (Server Settings → Integrations)."""
    return discord.Permissions.none()


async def main(bot, interaction: discord.Interaction, command: str) -> None:
    """Run a pipeline command: pick options in a form, confirm, then watch one live embed."""
    if not bot.policy.allowed(interaction):
        await deny(interaction, bot.policy.denial_message(interaction))
        return
    # Discord only exposes the pipeline tool; this slash is the lab-facing entry.
    spec = visible_tools(bot, interaction).get("pipeline") or bot.registry.tools.get("pipeline")
    error = bot.policy.tool_denial(interaction, spec) or (
        command_error(spec, command) if spec is not None else None
    )
    if error is not None:
        await deny(interaction, error)
        return
    await Launch(bot, spec, spec.commands[command]).open(interaction)


async def autocomplete_command(bot, interaction: discord.Interaction, current: str):
    """Autocomplete runnable pipeline commands on this surface."""
    spec = visible_tools(bot, interaction).get("pipeline")
    return autocomplete_choices(spec.runnable_commands(), current) if spec else []
