"""Shared helpers for command modules (not a slash command)."""
from __future__ import annotations

import discord
from discord import app_commands

from ..registry import ToolSpec


def autocomplete_choices(names, current: str) -> list[app_commands.Choice[str]]:
    """Filter names for Discord autocomplete (max 25 choices)."""
    needle = current.lower()
    hits = [name for name in names if needle in name.lower()]
    return [app_commands.Choice(name=name, value=name) for name in hits[:25]]


def visible_tools(bot, interaction: discord.Interaction) -> dict[str, ToolSpec]:
    """Tools whose config.toml exposes them here; nothing for untrusted contexts."""
    if not bot.policy.allowed(interaction):
        return {}
    return bot.registry.visible(interaction.guild_id is not None)


def command_error(tool: ToolSpec, command: str) -> str | None:
    """Return a user-facing error, or None if the command has a usable args.toml."""
    spec = tool.commands.get(command)
    if spec is None:
        return f"Unknown command: `{tool.name} {command}`. Try `/help`."
    if spec.schema_error:
        return f"`{tool.name} {command}` has a broken `args.toml`; ask the tool owner to fix it."
    if not spec.runnable:
        # Fail closed: no schema means nothing validates the arguments.
        return f"`{tool.name} {command}` has no `args.toml`, so it only runs from the terminal."
    return None


async def deny(interaction: discord.Interaction, message: str) -> None:
    """Refusals and errors are always private (ephemeral), never channel noise."""
    await interaction.response.send_message(message, ephemeral=True)


async def respond(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    ephemeral: bool,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> None:
    """Send the initial interaction response."""
    await interaction.response.send_message(
        content, ephemeral=ephemeral, embed=embed, view=view
    )
