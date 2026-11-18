"""Shared args/config contract for bahaa tools (one validator for CLI + hbc-bot).

Files next to the tools:

  tools/<tool>/config.toml            tool-level run properties (host, Discord surfaces)
  tools/<tool>/args.toml              optional arg specs merged under every command
  tools/<tool>/<command>/args.toml    command arg schema (required to run from Discord)

Stdlib only, no package-relative imports: this same file is vendored to
``tools/_lib/toolargs.py`` (with its bash hook ``toolargs.sh``) so ``tool.sh``
validates before ``run.sh``. Edit both in hbc-bot, then
``python -m hbc_bot.toolargs install <tools_dir>``.

CLI:
  toolargs.py argv <tool_dir> <command> -- [args...]  normalized argv, one per line (exit 2 if invalid)
  toolargs.py check <tool_dir>                        lint config.toml + every args.toml
  toolargs.py install <tools_dir>                     copy toolargs.py + toolargs.sh to <tools_dir>/_lib/
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ArgKind(str, Enum):
    """Closed set of arg kinds an args.toml may declare."""

    SELECT = "select"
    CONFIRM = "confirm"
    STRING = "string"
    STRING_LIST = "string_list"
    FILE = "file"


class Host(str, Enum):
    """Where a tool executes (config.toml ``[run] host``)."""

    VOSSLINK = "vosslink"
    ARGON = "argon"
    OTHER = "other"


class SchemaError(ValueError):
    """A config.toml / args.toml breaks the contract (tool author's bug)."""


class ArgsError(ValueError):
    """User-supplied args failed validation; ``problems`` lists every issue."""

    def __init__(self, problems: list[str]):
        """Keep the individual problems for bullet-style error output."""
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Choice:
    """One option of a ``select`` arg."""

    value: str
    label: str = ""
    help: str = ""
    # Literal tokens for switch-style selects; None → emitted as ``<flag> <value>``.
    argv: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ArgSpec:
    """One ``[[arg]]`` table from args.toml."""

    name: str
    kind: ArgKind
    label: str = ""
    help: str = ""
    flag: str = ""
    required: bool = False
    default: str | tuple[str, ...] | None = None
    choices: tuple[Choice, ...] = ()
    pattern: str = ""
    join: str = ","
    extensions: tuple[str, ...] = ()
    discord: bool = True
    danger: bool = False
    when: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_switch(self) -> bool:
        """True for a select whose choices map to literal flag sets (e.g. dry vs apply)."""
        return self.kind is ArgKind.SELECT and any(c.argv is not None for c in self.choices)

    @property
    def is_positional(self) -> bool:
        """True when the value is emitted bare (no ``--flag``)."""
        return self.kind is not ArgKind.CONFIRM and not self.flag and not self.is_switch

    def choice(self, value: str) -> Choice | None:
        """Look up a choice by its value."""
        return next((c for c in self.choices if c.value == value), None)


@dataclass(frozen=True)
class CommandSchema:
    """Merged arg schema for one ``<tool> <command>``."""

    args: tuple[ArgSpec, ...] = ()

    def value_args(self) -> list[ArgSpec]:
        """Args that carry a value into argv (everything except confirm)."""
        return [a for a in self.args if a.kind is not ArgKind.CONFIRM]

    def discord_fields(self) -> list[ArgSpec]:
        """Value args a Discord form should collect (``discord = false`` stays CLI-only)."""
        return [a for a in self.value_args() if a.discord]


@dataclass(frozen=True)
class ToolConfig:
    """Tool-level properties Discord itself cannot express (config.toml)."""

    host: Host = Host.VOSSLINK
    guild: bool = False
    dm: bool = False


def _toml():
    """tomllib on Python 3.11+, the tomli backport before that."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    return tomllib


def _read_toml(path: Path) -> dict:
    """Parse one toml file; syntax errors become SchemaError with the path."""
    toml = _toml()
    try:
        with path.open("rb") as handle:
            return toml.load(handle)
    except toml.TOMLDecodeError as exc:
        raise SchemaError(f"{path}: {exc}") from exc


def _only_keys(table: dict, allowed: set[str], where: str) -> None:
    """Reject unknown keys so typos fail loudly instead of being ignored."""
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise SchemaError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _typed(table: dict, key: str, kind: type, default, where: str):
    """Read ``table[key]`` enforcing its toml type (bool is not accepted as int/str)."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise SchemaError(f"{where}: `{key}` must be {kind.__name__}")
    return value


def _str_list(table: dict, key: str, where: str) -> tuple[str, ...] | None:
    """Read an optional list-of-strings key (None when absent)."""
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SchemaError(f"{where}: `{key}` must be a list of strings")
    return tuple(value)


def _is_flag(token: str) -> bool:
    """Long-option shape only: ``--name`` (lowercase, digits, hyphens)."""
    return bool(re.fullmatch(r"--[a-z0-9][a-z0-9-]*", token))


def load_tool_config(tool_dir: Path) -> ToolConfig | None:
    """Parse ``<tool_dir>/config.toml``. None when the file is missing (not on Discord)."""
    path = tool_dir / "config.toml"
    if not path.is_file():
        return None
    data = _read_toml(path)
    _only_keys(data, {"run", "discord"}, str(path))
    run = _typed(data, "run", dict, {}, str(path))
    surfaces = _typed(data, "discord", dict, {}, str(path))
    _only_keys(run, {"host"}, f"{path} [run]")
    _only_keys(surfaces, {"guild", "dm"}, f"{path} [discord]")
    host = _typed(run, "host", str, Host.VOSSLINK.value, f"{path} [run]")
    try:
        host_kind = Host(host)
    except ValueError:
        names = ", ".join(h.value for h in Host)
        raise SchemaError(f"{path} [run]: host must be one of {names}") from None
    return ToolConfig(
        host=host_kind,
        guild=_typed(surfaces, "guild", bool, False, f"{path} [discord]"),
        dm=_typed(surfaces, "dm", bool, False, f"{path} [discord]"),
    )


def _choice_from_table(table, where: str) -> Choice:
    """Build one select Choice from a ``[[arg.choice]]`` table."""
    if not isinstance(table, dict):
        raise SchemaError(f"{where}: each choice must be a table")
    _only_keys(table, {"value", "label", "help", "argv"}, where)
    value = _typed(table, "value", str, "", where)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise SchemaError(f"{where}: choice value {value!r} must be alphanumeric (._- allowed)")
    argv = _str_list(table, "argv", where)
    if argv is not None and not all(_is_flag(token) for token in argv):
        raise SchemaError(f"{where}: choice `{value}` argv must be --flags only")
    return Choice(
        value=value,
        label=_typed(table, "label", str, "", where),
        help=_typed(table, "help", str, "", where),
        argv=argv,
    )


def _when_from_table(table: dict, where: str) -> dict[str, tuple[str, ...]]:
    """Normalize ``when = { mode = ["apply"] }`` (a bare string means a one-item list)."""
    when: dict[str, tuple[str, ...]] = {}
    for key, value in table.items():
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise SchemaError(f"{where}: when.{key} must be a string or list of strings")
        when[key] = tuple(values)
    return when


def _spec_from_table(table, where: str) -> ArgSpec:
    """Build one ArgSpec from an ``[[arg]]`` table (shape checks only)."""
    if not isinstance(table, dict):
        raise SchemaError(f"{where}: each arg must be a table")
    name = _typed(table, "name", str, "", where)
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise SchemaError(f"{where}: arg name {name!r} must be lowercase letters/digits/_/-")
    where = f"{where} arg `{name}`"
    try:
        kind = ArgKind(_typed(table, "kind", str, "", where))
    except ValueError:
        kinds = ", ".join(k.value for k in ArgKind)
        raise SchemaError(f"{where}: kind must be one of {kinds}") from None

    # Keys allowed per kind; anything else is a typo or a kind mix-up.
    common = {"name", "kind", "label", "help"}
    value_keys = common | {"flag", "required", "default", "discord"}
    allowed = {
        ArgKind.CONFIRM: common | {"danger", "when"},
        ArgKind.SELECT: value_keys | {"choice"},
        ArgKind.STRING: value_keys | {"pattern"},
        ArgKind.STRING_LIST: value_keys | {"pattern", "join"},
        ArgKind.FILE: value_keys | {"extensions"},
    }[kind]
    _only_keys(table, allowed, where)

    default = table.get("default")
    if kind is ArgKind.STRING_LIST and default is not None:
        default = _str_list(table, "default", where)
    elif default is not None and not isinstance(default, str):
        raise SchemaError(f"{where}: `default` must be a string")
    choices = _typed(table, "choice", list, [], where)
    return ArgSpec(
        name=name,
        kind=kind,
        label=_typed(table, "label", str, "", where),
        help=_typed(table, "help", str, "", where),
        flag=_typed(table, "flag", str, "", where),
        required=_typed(table, "required", bool, False, where),
        default=default,
        choices=tuple(_choice_from_table(c, where) for c in choices),
        pattern=_typed(table, "pattern", str, "", where),
        join=_typed(table, "join", str, ",", where),
        extensions=_str_list(table, "extensions", where) or (),
        discord=_typed(table, "discord", bool, True, where),
        danger=_typed(table, "danger", bool, False, where),
        when=_when_from_table(_typed(table, "when", dict, {}, where), where),
    )


def _read_specs(path: Path) -> list[ArgSpec]:
    """Parse the ``[[arg]]`` list of one args.toml (an empty file means no args)."""
    data = _read_toml(path)
    _only_keys(data, {"arg"}, str(path))
    tables = _typed(data, "arg", list, [], str(path))
    return [_spec_from_table(table, str(path)) for table in tables]


def _merge_specs(tool_level: list[ArgSpec], command_level: list[ArgSpec], where: str) -> list[ArgSpec]:
    """Tool defaults first; a command arg with the same name replaces it in place."""
    names = [spec.name for spec in command_level]
    if len(names) != len(set(names)):
        raise SchemaError(f"{where}: duplicate arg name")
    merged = {spec.name: spec for spec in tool_level}
    merged.update({spec.name: spec for spec in command_level})
    return list(merged.values())


def _check_select(spec: ArgSpec, where: str) -> None:
    """Select rules: 1-25 unique choices, all switch-style or all value-style."""
    if not 1 <= len(spec.choices) <= 25:
        raise SchemaError(f"{where}: select needs 1-25 choices (Discord select limit)")
    values = [c.value for c in spec.choices]
    if len(values) != len(set(values)):
        raise SchemaError(f"{where}: duplicate choice value")
    if not spec.is_switch:
        return
    if any(c.argv is None for c in spec.choices):
        raise SchemaError(f"{where}: give `argv` on every choice or on none")
    if spec.flag:
        raise SchemaError(f"{where}: use either `flag` or per-choice `argv`, not both")
    token_sets = [frozenset(c.argv or ()) for c in spec.choices]
    if len(token_sets) != len(set(token_sets)):
        raise SchemaError(f"{where}: two choices map to the same argv")


def _check_default(spec: ArgSpec, where: str) -> None:
    """A declared default must itself be a valid value for the arg."""
    if spec.default is None:
        return
    try:
        _check_value(spec, spec.default, check_files=False)
    except ArgsError as exc:
        raise SchemaError(f"{where}: bad default ({exc})") from None


def _check_when(spec: ArgSpec, by_name: dict[str, ArgSpec], where: str) -> None:
    """Confirm ``when`` may only reference select args and their declared choices."""
    for key, wanted in spec.when.items():
        target = by_name.get(key)
        if target is None or target.kind is not ArgKind.SELECT:
            raise SchemaError(f"{where}: when.{key} must name a select arg")
        unknown = [value for value in wanted if target.choice(value) is None]
        if unknown:
            raise SchemaError(f"{where}: when.{key} has unknown choice(s) {', '.join(unknown)}")


def _build_schema(specs: list[ArgSpec], where: str) -> CommandSchema:
    """Cross-arg checks (unique flags, valid defaults, Discord form limit)."""
    by_name = {spec.name: spec for spec in specs}
    tokens: list[str] = []
    for spec in specs:
        here = f"{where} arg `{spec.name}`"
        if spec.flag and not _is_flag(spec.flag):
            raise SchemaError(f"{here}: flag {spec.flag!r} must look like --name")
        if spec.kind is ArgKind.SELECT:
            _check_select(spec, here)
        if spec.pattern:
            try:
                re.compile(spec.pattern)
            except re.error as exc:
                raise SchemaError(f"{here}: bad pattern ({exc})") from None
        if spec.kind is ArgKind.STRING_LIST and (len(spec.join) != 1 or spec.join.isspace()):
            raise SchemaError(f"{here}: join must be one non-space character")
        if not all(re.fullmatch(r"\.[A-Za-z0-9]+", ext) for ext in spec.extensions):
            raise SchemaError(f"{here}: extensions must look like .json")
        _check_default(spec, here)
        _check_when(spec, by_name, here)
        # The Discord form could never supply it, so every Discord run would fail.
        if spec.required and spec.default is None and not spec.discord:
            raise SchemaError(f"{here}: required + `discord = false` needs a default")
        tokens += [spec.flag] if spec.flag else []
        tokens += sorted({token for c in spec.choices for token in c.argv or ()})
    clashes = sorted({token for token in tokens if tokens.count(token) > 1})
    if clashes:
        raise SchemaError(f"{where}: flag(s) used by more than one arg: {', '.join(clashes)}")
    schema = CommandSchema(tuple(specs))
    # Discord modals hold at most 5 inputs; extra args must be CLI-only.
    if len(schema.discord_fields()) > 5:
        raise SchemaError(
            f"{where}: more than 5 Discord-visible args (modal limit); set `discord = false` on the rest"
        )
    return schema


def load_command_schema(tool_dir: Path, command: str) -> CommandSchema | None:
    """Schema for ``<tool_dir>/<command>/args.toml`` merged over the tool-level args.toml.

    None when the command has no args.toml (not runnable from Discord; CLI passthrough).
    """
    path = tool_dir / command / "args.toml"
    if not path.is_file():
        return None
    tool_path = tool_dir / "args.toml"
    tool_level = _read_specs(tool_path) if tool_path.is_file() else []
    specs = _merge_specs(tool_level, _read_specs(path), str(path))
    return _build_schema(specs, str(path))


def _check_text(spec: ArgSpec, value) -> str:
    """Validate one free-text value. Returns it stripped; raises ArgsError."""
    if not isinstance(value, str):
        raise ArgsError([f"{spec.name}: expected text"])
    text = value.strip()
    if not text:
        raise ArgsError([f"{spec.name}: empty value"])
    if len(text) > 1000:
        raise ArgsError([f"{spec.name}: longer than 1000 characters"])
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ArgsError([f"{spec.name}: control characters / newlines are not allowed"])
    # A leading dash could smuggle an extra option into the tool's argv.
    if text.startswith("-"):
        raise ArgsError([f"{spec.name}: value may not start with '-'"])
    if spec.pattern and not re.fullmatch(spec.pattern, text):
        raise ArgsError([f"{spec.name}: {text!r} does not match {spec.pattern}"])
    return text


def _split_list(spec: ArgSpec, value) -> list[str]:
    """Accept a list, or one string split on the join character and newlines."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if not isinstance(value, str):
        raise ArgsError([f"{spec.name}: expected a list"])
    parts = re.split(f"[{re.escape(spec.join)}\\r\\n]", value)
    return [part for part in (p.strip() for p in parts) if part]


def _check_value(spec: ArgSpec, value, *, check_files: bool = True) -> str | tuple[str, ...]:
    """Validate one present value for ``spec``. Returns the normalized value."""
    if spec.kind is ArgKind.SELECT:
        if not isinstance(value, str) or spec.choice(value) is None:
            options = ", ".join(c.value for c in spec.choices)
            raise ArgsError([f"{spec.name}: must be one of {options}"])
        return value
    if spec.kind is ArgKind.STRING_LIST:
        items = tuple(_check_text(spec, item) for item in _split_list(spec, value))
        if len(items) > 100:
            raise ArgsError([f"{spec.name}: more than 100 items"])
        if any(spec.join in item for item in items):
            raise ArgsError([f"{spec.name}: items may not contain {spec.join!r}"])
        return items
    text = _check_text(spec, value)
    if spec.kind is ArgKind.FILE:
        suffix = Path(text).suffix.lower()
        if spec.extensions and suffix not in {ext.lower() for ext in spec.extensions}:
            raise ArgsError([f"{spec.name}: file must end with {' / '.join(spec.extensions)}"])
        if check_files and not Path(text).is_file():
            raise ArgsError([f"{spec.name}: file not found: {text}"])
    return text


def validate(schema: CommandSchema, values: dict) -> dict[str, str | tuple[str, ...]]:
    """Check raw values (from CLI parse or a Discord form) and fill defaults.

    Returns normalized values for every arg that ends up set. Raises ArgsError
    listing every problem (unknown names, missing required, bad values).
    """
    problems: list[str] = []
    known = {spec.name for spec in schema.value_args()}
    problems += [f"unknown argument: {name}" for name in sorted(set(values) - known)]
    out: dict[str, str | tuple[str, ...]] = {}
    for spec in schema.value_args():
        value = values.get(spec.name)
        blank = isinstance(value, str) and not value.strip()
        present = not blank and value not in (None, [], ())
        if present:
            try:
                checked = _check_value(spec, value)
            except ArgsError as exc:
                problems += exc.problems
                continue
            # A list that was only separators counts as not given.
            present = checked != ()
        if present:
            out[spec.name] = checked
        elif spec.default is not None:
            out[spec.name] = spec.default
        elif spec.required:
            problems.append(f"{spec.name}: required")
    if problems:
        raise ArgsError(problems)
    return out


def to_argv(schema: CommandSchema, values: dict) -> list[str]:
    """Validated values → argv tokens in schema order (what run.sh receives)."""
    argv: list[str] = []
    for spec in schema.value_args():
        if spec.name not in values:
            continue
        value = values[spec.name]
        if spec.is_switch:
            argv += list(spec.choice(value).argv or ())
            continue
        text = spec.join.join(value) if spec.kind is ArgKind.STRING_LIST else value
        argv += [spec.flag, text] if spec.flag else [text]
    return argv


def confirm_for(schema: CommandSchema, values: dict) -> ArgSpec | None:
    """First confirm arg whose ``when`` matches the validated values (no ``when`` → always)."""
    for spec in schema.args:
        if spec.kind is not ArgKind.CONFIRM:
            continue
        if all(values.get(key) in wanted for key, wanted in spec.when.items()):
            return spec
    return None


def _switch_value(spec: ArgSpec, seen: list[str]) -> str:
    """Map the switch tokens present on the CLI to the one choice they spell."""
    for choice in spec.choices:
        if set(choice.argv or ()) == set(seen):
            return choice.value
    valid = " | ".join(" ".join(c.argv) or "(none)" for c in spec.choices)
    raise ArgsError([f"{spec.name}: invalid flag combination `{' '.join(seen)}`; valid: {valid}"])


def parse_argv(schema: CommandSchema, argv: list[str]) -> dict:
    """CLI tokens → raw values keyed by arg name. Raises ArgsError on unknown/duplicate tokens."""
    flags = {spec.flag: spec for spec in schema.value_args() if spec.flag}
    switches = {
        token: spec
        for spec in schema.value_args()
        for choice in spec.choices
        for token in choice.argv or ()
    }
    positionals = [spec for spec in schema.value_args() if spec.is_positional]
    values: dict = {}
    seen_switches: dict[str, list[str]] = {}
    problems: list[str] = []
    tokens = list(argv)
    while tokens:
        token = tokens.pop(0)
        name, eq, inline = token.partition("=")
        if token in switches:
            seen = seen_switches.setdefault(switches[token].name, [])
            if token in seen:
                problems.append(f"duplicate option: {token}")
            seen.append(token)
        elif name in flags:
            spec = flags[name]
            if not eq and not tokens:
                problems.append(f"{name}: missing value")
                continue
            value = inline if eq else tokens.pop(0)
            if spec.name in values:
                problems.append(f"duplicate option: {name}")
            values[spec.name] = value
        elif token.startswith("-"):
            problems.append(f"unknown option: {token}")
        elif positionals:
            values[positionals.pop(0).name] = token
        else:
            problems.append(f"unexpected argument: {token}")
    for name, seen in seen_switches.items():
        spec = next(s for s in schema.args if s.name == name)
        try:
            values[name] = _switch_value(spec, seen)
        except ArgsError as exc:
            problems += exc.problems
    if problems:
        raise ArgsError(problems)
    return values


def normalize_argv(schema: CommandSchema, argv: list[str]) -> list[str]:
    """CLI path: parse → validate → re-emit canonical argv. Raises ArgsError."""
    return to_argv(schema, validate(schema, parse_argv(schema, argv)))


def usage(schema: CommandSchema) -> str:
    """Plain-text arg summary for CLI error output."""
    rows = []
    for spec in schema.value_args():
        if spec.is_switch:
            form = " | ".join(" ".join(c.argv) or "(none)" for c in spec.choices)
        else:
            if spec.kind is ArgKind.SELECT:
                shape = "|".join(c.value for c in spec.choices)
            elif spec.kind is ArgKind.STRING_LIST:
                shape = f"a{spec.join}b{spec.join}..."
            else:
                shape = "PATH" if spec.kind is ArgKind.FILE else "VALUE"
            form = f"{spec.flag} {shape}" if spec.flag else f"<{spec.name}: {shape}>"
        default = spec.join.join(spec.default) if isinstance(spec.default, tuple) else spec.default
        notes = f"default: {default}" if default else "required" if spec.required else ""
        rows.append(f"  {spec.name:<20} {form}" + (f"   [{notes}]" if notes else ""))
    return "\n".join(rows) or "  (no arguments)"


def _cli_argv(tool_dir: Path, command: str, args: list[str]) -> int:
    """``argv`` subcommand: print normalized args one per line, or errors + exit 2."""
    try:
        schema = load_command_schema(tool_dir, command)
    except SchemaError as exc:
        print(f"toolargs: broken schema: {exc}", file=sys.stderr)
        return 2
    # No schema (legacy command) or a help request: hand argv through untouched.
    if schema is None or any(token in ("-h", "--help") for token in args):
        out = args
    else:
        try:
            out = normalize_argv(schema, args)
        except ArgsError as exc:
            print(f"bahaa {tool_dir.name} {command}: invalid arguments", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            print(f"Arguments ({tool_dir.name}/{command}/args.toml):\n{usage(schema)}", file=sys.stderr)
            return 2
    # Bytes + explicit \n: Windows Python would otherwise emit \r\n into bash arrays.
    sys.stdout.buffer.write("".join(f"{token}\n" for token in out).encode("utf-8"))
    return 0


def _cli_check(tool_dir: Path) -> int:
    """``check`` subcommand: lint config.toml and every command's args.toml."""
    failed = False
    try:
        config = load_tool_config(tool_dir)
        print(f"config.toml: {config if config else 'missing (tool not on Discord)'}")
    except SchemaError as exc:
        failed = True
        print(f"ERROR {exc}", file=sys.stderr)
    for run_sh in sorted(tool_dir.glob("*/run.sh")):
        command = run_sh.parent.name
        try:
            schema = load_command_schema(tool_dir, command)
        except SchemaError as exc:
            failed = True
            print(f"ERROR {exc}", file=sys.stderr)
            continue
        state = "no args.toml (CLI only)" if schema is None else f"ok ({len(schema.args)} args)"
        print(f"{command}: {state}")
    return 2 if failed else 0


def _cli_install(tools_dir: Path) -> int:
    """``install`` subcommand: vendor toolargs.py + toolargs.sh into ``<tools_dir>/_lib/``."""
    source_dir = Path(__file__).resolve().parent
    target_dir = tools_dir / "_lib"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ("toolargs.py", "toolargs.sh"):
        # Force LF: a Windows checkout may carry CRLF, which breaks bash on the lab hosts.
        data = (source_dir / name).read_bytes().replace(b"\r\n", b"\n")
        (target_dir / name).write_bytes(data)
        print(f"installed {target_dir / name}")
    return 0


def main(argv: list[str]) -> int:
    """CLI entry (see module docstring). Returns the process exit code."""
    if len(argv) >= 4 and argv[0] == "argv" and argv[3] == "--":
        return _cli_argv(Path(argv[1]), argv[2], argv[4:])
    if len(argv) == 2 and argv[0] == "check":
        return _cli_check(Path(argv[1]))
    if len(argv) == 2 and argv[0] == "install":
        return _cli_install(Path(argv[1]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
