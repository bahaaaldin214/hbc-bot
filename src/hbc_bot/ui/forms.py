"""args.toml → native Discord modal (one form per command).

Kind mapping (Discord collects; ``toolargs.validate`` decides):
  select       → String Select
  string       → Text Input (short)
  string_list  → Text Input (paragraph; comma / newline separated)
  file         → File Upload
``confirm`` is not a form field: it becomes a button (see ``PromptView``).
"""
from __future__ import annotations

import re
from pathlib import PurePath
from typing import Awaitable, Callable

import discord

from ..toolargs import ArgKind, ArgSpec


def upload_name(filename: str) -> str:
    """Reduce an uploaded file's name to a safe single path segment."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", PurePath(filename.replace("\\", "/")).name)
    return name.lstrip(".-") or "upload"


def _select(spec: ArgSpec, current: object) -> discord.ui.Select:
    """Dropdown for a ``select`` arg, preselecting the current/default value."""
    chosen = current if isinstance(current, str) and spec.choice(current) else spec.default
    options = [
        discord.SelectOption(
            label=(choice.label or choice.value)[:100],
            value=choice.value,
            description=choice.help[:100] or None,
            default=choice.value == chosen,
        )
        for choice in spec.choices
    ]
    # With a default the menu always carries a value; Discord wants min_values=0 otherwise.
    required = spec.required or spec.default is not None
    return discord.ui.Select(options=options, required=required, min_values=1 if required else 0)


def _text(spec: ArgSpec, current: object) -> discord.ui.TextInput:
    """Text box for ``string`` (one line) or ``string_list`` (paragraph)."""
    is_list = spec.kind is ArgKind.STRING_LIST
    default = current if isinstance(current, str) else spec.default
    if isinstance(default, tuple):
        default = f"{spec.join} ".join(default)
    return discord.ui.TextInput(
        style=discord.TextStyle.paragraph if is_list else discord.TextStyle.short,
        placeholder=f"separate with {spec.join!r} or new lines" if is_list else None,
        default=default or None,
        required=spec.required and spec.default is None,
        max_length=4000 if is_list else 1000,
    )


def _input_for(spec: ArgSpec, current: object) -> discord.ui.Item:
    """Pick the native modal component for one arg."""
    if spec.kind is ArgKind.SELECT:
        return _select(spec, current)
    if spec.kind is ArgKind.FILE:
        return discord.ui.FileUpload(required=spec.required, min_values=1 if spec.required else 0)
    return _text(spec, current)


class ArgsModal(discord.ui.Modal):
    """Native form for a command's Discord-visible args."""

    def __init__(
        self,
        title: str,
        fields: list[ArgSpec],
        values: dict[str, object],
        on_values: Callable[[discord.Interaction, dict], Awaitable[None]],
    ):
        """Build one labelled input per arg (max 5 per Discord; enforced at schema load).

        ``values`` prefills the form on re-edit; ``on_values`` receives the raw
        submission (strings, or a ``discord.Attachment`` for file args).
        """
        super().__init__(title=title[:45], timeout=900)
        self._on_values = on_values
        self._inputs: dict[str, discord.ui.Item] = {}
        for spec in fields:
            item = _input_for(spec, values.get(spec.name))
            self._inputs[spec.name] = item
            self.add_item(
                discord.ui.Label(
                    text=(spec.label or spec.name)[:45],
                    description=spec.help[:100] or None,
                    component=item,
                )
            )

    def raw_values(self) -> dict[str, object]:
        """Submitted values keyed by arg name; untouched optional fields are omitted."""
        raw: dict[str, object] = {}
        for name, item in self._inputs.items():
            if isinstance(item, discord.ui.TextInput):
                value = item.value
            else:  # Select / FileUpload both expose a list
                value = item.values[0] if item.values else None
            if value:
                raw[name] = value
        return raw

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hand the raw submission to the launch flow (validation happens there)."""
        await self._on_values(interaction, self.raw_values())
