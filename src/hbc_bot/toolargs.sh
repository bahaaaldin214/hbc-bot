#!/usr/bin/env bash
# Shared args gate for bahaa tools (bash side of toolargs.py).
#
# Source of truth: hbc-bot `src/hbc_bot/toolargs.{py,sh}`. Vendored to `tools/_lib/` with
#   python -m hbc_bot.toolargs install <tools_dir>
# Do not edit the copy under tools/_lib/.
#
# Use in a tool.sh, right before run.sh:
#
#   source "${TOOL_DIR}/../_lib/toolargs.sh"
#   toolargs_normalize "${TOOL_DIR}" "${COMMAND}" "$@" || exit 2
#   bash "${COMMAND_SCRIPT}" ${TOOLARGS_ARGV[@]+"${TOOLARGS_ARGV[@]}"}
#
# <command>/args.toml present -> argv is validated and normalized (bad args: exit 2).
# No args.toml                -> argv passes through unchanged (legacy CLI command).

# Print the first Python that can read toml (3.11+ tomllib, or tomli installed).
_toolargs_python() {
  local py
  local candidates=("${TOOLARGS_PYTHON:-}" python3 python)
  # conda base next to CONDA_SH (boost-env.sh) is a modern Python on vosslink.
  if [[ -n "${CONDA_SH:-}" ]]; then
    candidates+=("${CONDA_SH%/etc/profile.d/conda.sh}/bin/python")
  fi
  for py in "${candidates[@]}"; do
    [[ -n "${py}" ]] || continue
    if "${py}" -c 'import tomllib' >/dev/null 2>&1 || "${py}" -c 'import tomli' >/dev/null 2>&1; then
      printf '%s\n' "${py}"
      return 0
    fi
  done
  return 1
}

# toolargs_normalize <tool_dir> <command> [args...]  -> sets TOOLARGS_ARGV (array).
# Returns 2 when the args are invalid or the validator cannot run (fail closed).
toolargs_normalize() {
  local tool_dir="$1" command="$2"
  shift 2
  TOOLARGS_ARGV=("$@")
  [[ -f "${tool_dir}/${command}/args.toml" ]] || return 0

  local lib_dir py out
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if ! py="$(_toolargs_python)"; then
    echo "toolargs: ${command} has args.toml but no Python with tomllib (>= 3.11) was found." >&2
    echo "toolargs: set TOOLARGS_PYTHON=/path/to/python and retry." >&2
    return 2
  fi
  # One normalized arg per line (the validator rejects newlines inside values).
  out="$("${py}" "${lib_dir}/toolargs.py" argv "${tool_dir}" "${command}" -- "$@")" || return 2
  TOOLARGS_ARGV=()
  if [[ -n "${out}" ]]; then
    mapfile -t TOOLARGS_ARGV <<< "${out}"
  fi
  return 0
}
