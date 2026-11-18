"""List bahaa tools or show one tool's HELP.md."""
from __future__ import annotations

import discord

from ..ui import embeds
from ._common import autocomplete_choices, deny, respond, visible_tools


def option_help() -> dict[str, str]:
    """Slash option descriptions for /help."""
    return {"tool": "Tool name (leave empty to list all tools)"}


async def main(bot, interaction: discord.Interaction, tool: str = "") -> None:
    """List the tools available here, or detailed help for one tool name."""
    ephemeral = bot.cfg.ephemeral_for(interaction.channel_id, interaction.guild_id)
    # Lab server: anyone can read help (no default_permissions). DMs: allowlist only.
    if not bot.policy.allowed(interaction):
        await deny(interaction, bot.policy.denial_message(interaction))
        return

    available = visible_tools(bot, interaction)
    if not tool:
        await respond(interaction, ephemeral=ephemeral, embed=_list_embed(available))
        return
    if tool not in available:
        await deny(interaction, f"`{tool}` is not available here.")
        return
    await respond(
        interaction,
        ephemeral=ephemeral,
        embed=embeds.help_embed(tool, available[tool].help),
    )


def _list_embed(available: dict) -> discord.Embed:
    """Build the all-tools overview embed."""
    embed = discord.Embed(title="bahaa tools", color=discord.Color.blurple())
    rows = [_tool_row(spec) for spec in available.values()]
    embed.description = (
        "\n".join(rows) or "No tools here. A tool needs `config.toml` to appear on Discord."
    )[:4000]
    embed.set_footer(text="Run with /pipeline · details with /help tool:<name>")
    return embed


def _tool_row(spec) -> str:
    """One overview row: runnable commands listed, terminal-only ones just counted."""
    runnable = spec.runnable_commands()
    row = f"**{spec.name}** — {spec.summary or 'no description'}\n"
    row += " ".join(f"`{name}`" for name in runnable) or "*no commands runnable from Discord yet*"
    cli_only = len(spec.commands) - len(runnable)
    return row + (f" (+{cli_only} terminal-only, no `args.toml`)" if cli_only else "")


async def autocomplete_tool(bot, interaction: discord.Interaction, current: str):
    """Autocomplete only tools visible in this context."""
    return autocomplete_choices(visible_tools(bot, interaction), current)
