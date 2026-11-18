"""Discover bahaa tools from the tools tree.

Convention (same as the `bahaa` launcher):
  tools/<tool>/tool.sh            -> <tool> is a tool
  tools/<tool>/<command>/run.sh   -> <command> is a command of that tool
  tools/<tool>/HELP.md            -> help text (optional; also per command)
  tools/<tool>/config.toml        -> Discord surfaces + host (missing -> not on Discord)
  tools/<tool>/<command>/args.toml -> arg schema (missing -> not runnable from Discord)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import toolargs

log = logging.getLogger(__name__)


def _safe_name(name: str) -> bool:
    """True if ``name`` is a safe tool/command path segment (no shell metacharacters)."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name))


def _read(path: Path) -> str:
    """Read a text file, or return empty string if missing/unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _summary(text: str) -> str:
    """First prose line of a help file (skip headings/fences)."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "```", "---")):
            return line[:100]
    return ""


@dataclass(frozen=True)
class CommandSpec:
    """One ``<tool>/<command>/run.sh`` entry."""

    name: str
    summary: str = ""
    help: str = ""
    schema: toolargs.CommandSchema | None = None
    schema_error: str = ""

    @property
    def runnable(self) -> bool:
        """True when a valid args.toml exists (fail closed otherwise)."""
        return self.schema is not None


@dataclass(frozen=True)
class ToolSpec:
    """One discovered bahaa tool and its commands."""

    name: str
    summary: str = ""
    help: str = ""
    commands: dict[str, CommandSpec] = field(default_factory=dict)
    config: toolargs.ToolConfig | None = None
    config_error: str = ""

    def on_surface(self, in_guild: bool) -> bool:
        """True when config.toml exposes this tool to the lab server (or to DMs)."""
        if self.config is None:
            return False
        return self.config.guild if in_guild else self.config.dm

    def runnable_commands(self) -> dict[str, CommandSpec]:
        """Commands that have a valid args.toml."""
        return {name: spec for name, spec in self.commands.items() if spec.runnable}

    def problems(self) -> list[str]:
        """Broken config.toml / args.toml messages for this tool."""
        errors = [self.config_error] if self.config_error else []
        return errors + [spec.schema_error for spec in self.commands.values() if spec.schema_error]


def _load_schema(tool_dir: Path, command: str) -> tuple[toolargs.CommandSchema | None, str]:
    """Load one command schema; a broken file becomes (None, error) and is logged."""
    try:
        return toolargs.load_command_schema(tool_dir, command), ""
    except (toolargs.SchemaError, OSError) as exc:
        log.warning("bad args.toml: %s", exc)
        return None, str(exc)


def _load_config(tool_dir: Path) -> tuple[toolargs.ToolConfig | None, str]:
    """Load config.toml; a broken file becomes (None, error) and is logged."""
    try:
        return toolargs.load_tool_config(tool_dir), ""
    except (toolargs.SchemaError, OSError) as exc:
        log.warning("bad config.toml: %s", exc)
        return None, str(exc)


class Registry:
    """In-memory index of tools under ``tools_dir``."""

    def __init__(self, tools_dir: Path):
        """Create an empty registry pointed at ``tools_dir``."""
        self.tools_dir = tools_dir
        self.tools: dict[str, ToolSpec] = {}

    def scan(self) -> int:
        """Rescan the tools tree. Returns the number of tools found."""
        tools: dict[str, ToolSpec] = {}
        if self.tools_dir.is_dir():
            for tool_dir in sorted(self.tools_dir.iterdir()):
                if not _safe_name(tool_dir.name) or not (tool_dir / "tool.sh").is_file():
                    continue
                commands = {}
                for cmd_dir in sorted(tool_dir.iterdir()):
                    if _safe_name(cmd_dir.name) and (cmd_dir / "run.sh").is_file():
                        text = _read(cmd_dir / "HELP.md")
                        schema, error = _load_schema(tool_dir, cmd_dir.name)
                        commands[cmd_dir.name] = CommandSpec(
                            cmd_dir.name, _summary(text), text, schema, error
                        )
                text = _read(tool_dir / "HELP.md")
                config, error = _load_config(tool_dir)
                tools[tool_dir.name] = ToolSpec(
                    tool_dir.name, _summary(text), text, commands, config, error
                )
        self.tools = tools
        return len(tools)

    def get(self, tool: str, command: str | None = None) -> ToolSpec | CommandSpec | None:
        """Look up a tool, or a command within a tool when ``command`` is set."""
        spec = self.tools.get(tool)
        if spec is None or command is None:
            return spec
        return spec.commands.get(command)

    def visible(self, in_guild: bool) -> dict[str, ToolSpec]:
        """Tools whose config.toml exposes them on this surface (lab server vs DM)."""
        return {name: spec for name, spec in self.tools.items() if spec.on_surface(in_guild)}

    def problems(self) -> list[str]:
        """Every broken config.toml / args.toml found by the last scan."""
        return [line for spec in self.tools.values() for line in spec.problems()]
