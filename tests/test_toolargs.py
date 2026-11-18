"""Shared args validator: toml loading, merge, CLI parse, validation, argv output."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hbc_bot import toolargs
from hbc_bot.toolargs import ArgsError, Host, SchemaError


def _pilot_dir() -> Path:
    """Mirror of the live ``tools/pipeline`` pilot toml files."""
    return Path(__file__).resolve().parent / "fixtures" / "tools" / "pipeline"


def _debrief() -> toolargs.CommandSchema:
    """Schema of the pilot ``pipeline debrief`` command."""
    schema = toolargs.load_command_schema(_pilot_dir(), "debrief")
    assert schema is not None
    return schema


def _tool(tmp_path: Path, command_toml: str, tool_toml: str | None = None) -> Path:
    """Write a throwaway tool dir with one command ``run`` and return it."""
    tool_dir = tmp_path / "demo"
    (tool_dir / "run").mkdir(parents=True)
    (tool_dir / "run" / "args.toml").write_text(command_toml, encoding="utf-8")
    if tool_toml is not None:
        (tool_dir / "args.toml").write_text(tool_toml, encoding="utf-8")
    return tool_dir


def test_pilot_config() -> None:
    """Pilot config.toml exposes pipeline on both surfaces and pins vosslink."""
    config = toolargs.load_tool_config(_pilot_dir())
    assert config == toolargs.ToolConfig(host=Host.VOSSLINK, guild=True, dm=True)


def test_config_missing_and_strict(tmp_path: Path) -> None:
    """No config.toml → None; unknown keys and bad hosts are schema errors."""
    assert toolargs.load_tool_config(tmp_path) is None
    (tmp_path / "config.toml").write_text("[discord]\ndms = true\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="unknown key"):
        toolargs.load_tool_config(tmp_path)
    (tmp_path / "config.toml").write_text('[run]\nhost = "laptop"\n', encoding="utf-8")
    with pytest.raises(SchemaError, match="host must be one of"):
        toolargs.load_tool_config(tmp_path)


def test_pilot_discord_fields() -> None:
    """Only mode/study/ids/limit reach the Discord form; the rest is CLI-only."""
    names = [spec.name for spec in _debrief().discord_fields()]
    assert names == ["mode", "study", "ids", "limit"]


@pytest.mark.parametrize(
    ("cli", "expected"),
    [
        # plain dry run: defaults are spelled out
        ([], ["--study", "int", "--auto-fitness"]),
        # weekly cron line
        (
            ["--apply", "--land", "--study", "int"],
            ["--apply", "--land", "--study", "int", "--auto-fitness"],
        ),
        # agent ali4 / gird callers
        (
            ["--apply", "--land", "--ids", "8080,9031"],
            ["--apply", "--land", "--study", "int", "--ids", "8080,9031", "--auto-fitness"],
        ),
        (
            ["--study=int", "--ids", "sub-8080", "--force", "--no-auto-fitness"],
            ["--study", "int", "--ids", "sub-8080", "--no-auto-fitness", "--force"],
        ),
        (["--limit", "3", "--study", "both"], ["--study", "both", "--limit", "3", "--auto-fitness"]),
    ],
)
def test_pilot_cli_normalize(cli: list[str], expected: list[str]) -> None:
    """Real debrief invocations normalize to one canonical argv."""
    assert toolargs.normalize_argv(_debrief(), cli) == expected


@pytest.mark.parametrize(
    ("cli", "problem"),
    [
        (["--bogus"], "unknown option: --bogus"),
        (["--apply"], "invalid flag combination"),
        (["--land"], "invalid flag combination"),
        (["--study", "ext"], "study: must be one of int, obs, both"),
        (["--ids", "80"], "ids: '80' does not match"),
        (["--ids"], "--ids: missing value"),
        (["--limit", "-1"], "limit: value may not start with '-'"),
        (["stray"], "unexpected argument: stray"),
        (["--study", "int", "--study", "obs"], "duplicate option: --study"),
        (["--layout", "x"], "unknown option: --layout"),
        (["--manual-json", "nope.json"], "manual-json: file not found"),
        (["--auto-fitness", "--no-auto-fitness"], "invalid flag combination"),
    ],
)
def test_pilot_cli_rejects(cli: list[str], problem: str) -> None:
    """Unknown flags, bad combos and bad values are all rejected."""
    with pytest.raises(ArgsError) as info:
        toolargs.normalize_argv(_debrief(), cli)
    assert any(problem in line for line in info.value.problems), info.value.problems


def test_discord_values_path() -> None:
    """Form values (strings) validate to the same argv the CLI produces."""
    schema = _debrief()
    values = toolargs.validate(schema, {"mode": "apply", "ids": "8080, 9031\n8002", "limit": " 5 "})
    assert values["ids"] == ("8080", "9031", "8002")
    assert values["limit"] == "5"
    assert toolargs.to_argv(schema, values) == [
        "--apply", "--land", "--study", "int", "--ids", "8080,9031,8002",
        "--limit", "5", "--auto-fitness",
    ]
    with pytest.raises(ArgsError) as info:
        toolargs.validate(schema, {"mode": "wipe", "nope": "1", "ids": "8080;rm"})
    assert len(info.value.problems) == 3


def test_confirm_only_on_apply() -> None:
    """The confirm button applies to mode=apply, not to a dry run."""
    schema = _debrief()
    dry = toolargs.validate(schema, {})
    apply = toolargs.validate(schema, {"mode": "apply"})
    assert toolargs.confirm_for(schema, dry) is None
    confirm = toolargs.confirm_for(schema, apply)
    assert confirm is not None and confirm.danger and confirm.label == "Apply + land PDFs"


def test_file_kind(tmp_path: Path) -> None:
    """File args need an existing path with an allowed extension."""
    schema = _debrief()
    good = tmp_path / "manual.json"
    good.write_text("{}", encoding="utf-8")
    bad = tmp_path / "manual.txt"
    bad.write_text("x", encoding="utf-8")
    values = toolargs.validate(schema, {"manual-json": str(good)})
    assert "--manual-json" in toolargs.to_argv(schema, values)
    with pytest.raises(ArgsError, match="must end with .json"):
        toolargs.validate(schema, {"manual-json": str(bad)})


def test_merge_tool_defaults_and_positionals(tmp_path: Path) -> None:
    """Tool-level args.toml merges under the command; same name → command wins."""
    tool_toml = """
[[arg]]
name = "site"
kind = "select"
flag = "--site"
default = "ui"
  [[arg.choice]]
  value = "ui"
  [[arg.choice]]
  value = "neu"

[[arg]]
name = "note"
kind = "string"
flag = "--note"
"""
    command_toml = """
[[arg]]
name = "note"
kind = "string"
flag = "--why"
required = true

[[arg]]
name = "target"
kind = "string"
required = true

[[arg]]
name = "go"
kind = "confirm"
"""
    schema = toolargs.load_command_schema(_tool(tmp_path, command_toml, tool_toml), "run")
    assert [spec.name for spec in schema.args] == ["site", "note", "target", "go"]
    assert toolargs.normalize_argv(schema, ["sub-8080", "--why", "qa"]) == [
        "--site", "ui", "--why", "qa", "sub-8080",
    ]
    with pytest.raises(ArgsError) as info:
        toolargs.normalize_argv(schema, ["--note", "x"])
    assert "unknown option: --note" in info.value.problems
    # confirm without `when` always applies; it never reaches argv
    assert toolargs.confirm_for(schema, {}).name == "go"


def test_no_args_toml_means_not_runnable(tmp_path: Path) -> None:
    """A command without args.toml has no schema; an empty file means 'no args'."""
    tool_dir = _tool(tmp_path, "")
    assert toolargs.load_command_schema(tool_dir, "missing") is None
    schema = toolargs.load_command_schema(tool_dir, "run")
    assert schema.args == ()
    assert toolargs.normalize_argv(schema, []) == []
    with pytest.raises(ArgsError):
        toolargs.normalize_argv(schema, ["anything"])


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[arg]]\nname = "x"\nkind = "toggle"\n', "kind must be one of"),
        ('[[arg]]\nname = "X"\nkind = "string"\n', "arg name"),
        ('[[arg]]\nname = "x"\nkind = "string"\nchoices = []\n', "unknown key"),
        ('[[arg]]\nname = "x"\nkind = "string"\nflag = "-x"\n', "must look like --name"),
        ('[[arg]]\nname = "x"\nkind = "select"\n', "1-25 choices"),
        (
            '[[arg]]\nname = "x"\nkind = "select"\ndefault = "c"\n'
            '[[arg.choice]]\nvalue = "a"\n[[arg.choice]]\nvalue = "b"\n',
            "bad default",
        ),
        (
            '[[arg]]\nname = "x"\nkind = "select"\n'
            '[[arg.choice]]\nvalue = "a"\nargv = []\n[[arg.choice]]\nvalue = "b"\n',
            "every choice or on none",
        ),
        (
            '[[arg]]\nname = "x"\nkind = "select"\n'
            '[[arg.choice]]\nvalue = "a"\nargv = ["--go"]\n[[arg.choice]]\nvalue = "b"\nargv = ["--go"]\n',
            "same argv",
        ),
        (
            '[[arg]]\nname = "x"\nkind = "string"\nflag = "--v"\n'
            '[[arg]]\nname = "y"\nkind = "string"\nflag = "--v"\n',
            "more than one arg",
        ),
        ('[[arg]]\nname = "c"\nkind = "confirm"\nwhen = { mode = "apply" }\n', "must name a select"),
        ('[[arg]]\nname = "x"\nkind = "string"\npattern = "("\n', "bad pattern"),
        ("[[arg]\n", "args.toml"),
        (
            '[[arg]]\nname = "x"\nkind = "string"\nflag = "--x"\nrequired = true\ndiscord = false\n',
            "needs a default",
        ),
        (
            "".join(f'[[arg]]\nname = "a{i}"\nkind = "string"\nflag = "--a{i}"\n' for i in range(6)),
            "more than 5 Discord-visible",
        ),
    ],
)
def test_schema_errors(tmp_path: Path, body: str, message: str) -> None:
    """Broken toml fails at load with a pointed message (never silently ignored)."""
    with pytest.raises(SchemaError, match=message):
        toolargs.load_command_schema(_tool(tmp_path, body), "run")


def test_cli_argv(tmp_path: Path, capfdbinary) -> None:
    """`toolargs.py argv` prints LF-separated args, exits 2 on bad args, passes --help through."""
    pilot = str(_pilot_dir())
    assert toolargs.main(["argv", pilot, "debrief", "--", "--apply", "--land"]) == 0
    out = capfdbinary.readouterr().out
    assert out == b"--apply\n--land\n--study\nint\n--auto-fitness\n"

    assert toolargs.main(["argv", pilot, "debrief", "--", "--bogus"]) == 2
    captured = capfdbinary.readouterr()
    assert captured.out == b""
    assert b"unknown option: --bogus" in captured.err and b"--study int|obs|both" in captured.err

    assert toolargs.main(["argv", pilot, "debrief", "--", "--help"]) == 0
    assert capfdbinary.readouterr().out == b"--help\n"

    # legacy command without args.toml: argv passes through untouched
    legacy = _tool(tmp_path, "")
    assert toolargs.main(["argv", str(legacy), "other", "--", "--anything", "x"]) == 0
    assert capfdbinary.readouterr().out == b"--anything\nx\n"
    assert toolargs.main(["nonsense"]) == 2


def test_cli_check_and_install(tmp_path: Path, capsys) -> None:
    """`check` lints a tool dir; `install` vendors the validator into _lib/."""
    (_tool(tmp_path, "") / "run" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    assert toolargs.main(["check", str(tmp_path / "demo")]) == 0
    assert "run: ok (0 args)" in capsys.readouterr().out
    (tmp_path / "demo" / "run" / "args.toml").write_text("[[arg]]\nname = 1\n", encoding="utf-8")
    assert toolargs.main(["check", str(tmp_path / "demo")]) == 2

    assert toolargs.main(["install", str(tmp_path)]) == 0
    for name in ("toolargs.py", "toolargs.sh"):
        vendored = (tmp_path / "_lib" / name).read_bytes()
        source = Path(toolargs.__file__).with_name(name).read_bytes()
        assert vendored == source.replace(b"\r\n", b"\n") and b"\r" not in vendored


def _bash() -> str | None:
    """Prefer Git Bash on Windows; otherwise PATH bash."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    return str(git_bash) if git_bash.is_file() else shutil.which("bash")


@pytest.mark.skipif(_bash() is None, reason="bash not available")
def test_tool_sh_hook_end_to_end(tmp_path: Path) -> None:
    """bahaa → tool.sh → toolargs.sh → run.sh: normalized argv in, bad args stopped with exit 2."""
    tools = tmp_path / "tools"
    shutil.copytree(_pilot_dir().parent, tools)
    assert toolargs.main(["install", str(tools)]) == 0
    tool_sh = """#!/usr/bin/env bash
set -euo pipefail
TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND="$1"
shift
source "${TOOL_DIR}/../_lib/toolargs.sh"
toolargs_normalize "${TOOL_DIR}" "${COMMAND}" "$@" || exit 2
bash "${TOOL_DIR}/${COMMAND}/run.sh" ${TOOLARGS_ARGV[@]+"${TOOLARGS_ARGV[@]}"}
"""
    (tools / "pipeline" / "tool.sh").write_text(tool_sh, encoding="utf-8", newline="\n")

    def run(*args: str) -> subprocess.CompletedProcess:
        """Invoke the fixture tool.sh the way the bahaa launcher does."""
        env = {**os.environ, "TOOLARGS_PYTHON": sys.executable}
        return subprocess.run(
            [_bash(), str(tools / "pipeline" / "tool.sh"), *args],
            capture_output=True, text=True, env=env, check=False,
        )

    ok = run("debrief", "--apply", "--land", "--ids", "8080,9031")
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == "debrief --apply --land --study int --ids 8080,9031 --auto-fitness"

    bad = run("debrief", "--apply")
    assert bad.returncode == 2 and bad.stdout == ""
    assert "invalid flag combination" in bad.stderr

    # No args.toml → legacy passthrough, argv untouched (including no-arg calls).
    legacy = run("ggir", "--whatever", "x y")
    assert legacy.returncode == 0 and legacy.stdout.strip() == "ggir --whatever x y"
    assert run("ggir").stdout.strip() == "ggir"
