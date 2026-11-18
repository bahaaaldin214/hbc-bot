"""Discover Discord slash commands from ``commands/`` (filename → command).

Convention (mirrors bahaa tools discovery):

  commands/<name>.py              → /<name>  (must define async def main(bot, interaction, ...))
  commands/<group>/<name>.py      → /<group> <name>
  commands/<group>/__init__.py    → optional async def check(bot, interaction) -> bool
  commands/_*.py                  → private helpers (not commands)

Optional per module:
  option_help() -> dict[str, str]           parameter descriptions
  autocomplete_<param>(bot, interaction, current) -> choices
  default_permissions() -> discord.Permissions
      Discord-native gate (top-level command file or group __init__.py only; Discord
      has no per-subcommand permissions). ``Permissions.none()`` = admins only until
      the server grants roles/channels under Server Settings → Integrations.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from types import ModuleType

import discord
from discord import app_commands

log = logging.getLogger(__name__)


def _safe_command_name(name: str) -> bool:
    """Discord slash names: lowercase letters, digits, hyphen/underscore."""
    return bool(name) and name[0].isalpha() and all(c.isalnum() or c in "-_" for c in name)


def _first_doc_line(obj: object, fallback: str) -> str:
    """First line of a docstring, capped for Discord's description limit."""
    doc = inspect.getdoc(obj) or ""
    line = doc.splitlines()[0].strip() if doc else ""
    return (line or fallback)[:100]


def _import_module(dotted: str) -> ModuleType:
    """Import a module by dotted path."""
    return importlib.import_module(dotted)


def _wrap_main(bot, main_fn):
    """Build a discord.py callback: (interaction, …options) → main(bot, interaction, …)."""
    sig = inspect.signature(main_fn)
    params = list(sig.parameters.values())
    if len(params) < 2 or params[0].name != "bot" or params[1].name != "interaction":
        raise ValueError(f"{main_fn.__module__}.main must start with (bot, interaction, ...)")

    option_params = params[2:]
    new_params = [
        inspect.Parameter(
            "interaction",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=discord.Interaction,
        ),
        *option_params,
    ]
    new_sig = sig.replace(parameters=new_params)

    async def callback(interaction: discord.Interaction, *args, **kwargs):
        """Dispatch into the command module's main(bot, interaction, …)."""
        return await main_fn(bot, interaction, *args, **kwargs)

    callback.__signature__ = new_sig  # type: ignore[attr-defined]
    callback.__doc__ = main_fn.__doc__
    annotations = {"interaction": discord.Interaction}
    for param in option_params:
        if param.annotation is not inspect.Parameter.empty:
            annotations[param.name] = param.annotation
    callback.__annotations__ = annotations
    return callback, [p.name for p in option_params]


def _attach_option_help(callback, module: ModuleType) -> None:
    """Copy option_help() descriptions onto the callback for discord.py."""
    helper = getattr(module, "option_help", None)
    if helper is None:
        return
    docs = helper()
    if not isinstance(docs, dict):
        raise TypeError(f"{module.__name__}.option_help must return dict[str, str]")
    callback.__discord_app_commands_param_description__ = {
        str(key): str(value) for key, value in docs.items()
    }


def _autocomplete_adapter(bot, ac_fn):
    """Wrap module autocomplete_(bot, interaction, current) for discord.py (2 args)."""

    async def autocomplete(interaction: discord.Interaction, current: str):
        """Forward Discord autocomplete to the command module helper."""
        return await ac_fn(bot, interaction, current)

    return autocomplete


def _attach_autocompletes(
    command: app_commands.Command,
    bot,
    module: ModuleType,
    option_names: list[str],
) -> None:
    """Wire autocomplete_<param> helpers when present."""
    for name in option_names:
        ac = getattr(module, f"autocomplete_{name}", None)
        if ac is None:
            continue
        command.autocomplete(name)(_autocomplete_adapter(bot, ac))


def _apply_default_permissions(
    command: app_commands.Command | app_commands.Group, module: ModuleType
) -> None:
    """Set default_member_permissions from the module's default_permissions() hook."""
    helper = getattr(module, "default_permissions", None)
    if helper is None:
        return
    perms = helper()
    if not isinstance(perms, discord.Permissions):
        raise TypeError(f"{module.__name__}.default_permissions must return discord.Permissions")
    command.default_permissions = perms


def _build_command(bot, module: ModuleType, name: str) -> app_commands.Command:
    """Turn a module with main() into an app_commands.Command."""
    main_fn = getattr(module, "main", None)
    if main_fn is None or not inspect.iscoroutinefunction(main_fn):
        raise ValueError(f"{module.__name__} must define async def main(...)")
    if not _safe_command_name(name):
        raise ValueError(f"invalid command name from file: {name!r}")

    callback, option_names = _wrap_main(bot, main_fn)
    _attach_option_help(callback, module)
    command = app_commands.Command(
        name=name,
        description=_first_doc_line(main_fn, name),
        callback=callback,
    )
    _attach_autocompletes(command, bot, module, option_names)
    return command


def _load_group(bot, package_name: str, group_name: str) -> app_commands.Group:
    """Load commands/<group>/*.py as subcommands of /group."""
    pkg = _import_module(package_name)
    group = app_commands.Group(
        name=group_name,
        description=_first_doc_line(pkg, group_name),
    )

    check_fn = getattr(pkg, "check", None)
    if check_fn is not None:
        if not inspect.iscoroutinefunction(check_fn):
            raise ValueError(f"{package_name}.check must be async")

        async def interaction_check(interaction: discord.Interaction, _check=check_fn) -> bool:
            """Group-level gate from commands/<group>/__init__.py."""
            return await _check(bot, interaction)

        group.interaction_check = interaction_check

    _apply_default_permissions(group, pkg)
    pkg_path = Path(pkg.__file__).resolve().parent
    for path in sorted(pkg_path.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        sub_name = path.stem
        dotted = f"{package_name}.{sub_name}"
        module = _import_module(dotted)
        group.add_command(_build_command(bot, module, sub_name))
        log.info("loaded /%s %s from %s", group_name, sub_name, dotted)

    return group


def load_commands(bot, tree: app_commands.CommandTree) -> int:
    """Scan ``hbc_bot.commands`` and register slash commands on ``tree``. Returns count."""
    package_name = "hbc_bot.commands"
    package = _import_module(package_name)
    root = Path(package.__file__).resolve().parent
    count = 0

    # Top-level command modules: commands/foo.py → /foo
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        name = path.stem
        dotted = f"{package_name}.{name}"
        module = _import_module(dotted)
        command = _build_command(bot, module, name)
        _apply_default_permissions(command, module)
        tree.add_command(command)
        log.info("loaded /%s from %s", name, dotted)
        count += 1

    # Groups: commands/foo/*.py → /foo <sub>
    for entry in sorted(pkgutil.iter_modules([str(root)])):
        if not entry.ispkg or entry.name.startswith("_"):
            continue
        group_name = entry.name
        if not _safe_command_name(group_name):
            raise ValueError(f"invalid group name: {group_name!r}")
        group = _load_group(bot, f"{package_name}.{group_name}", group_name)
        tree.add_command(group)
        count += len(group.commands)
        log.info("loaded group /%s (%d subcommands)", group_name, len(group.commands))

    return count
