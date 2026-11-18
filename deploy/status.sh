#!/usr/bin/env bash
# Exit 0 if bot process looks alive.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="hbc-bot.service"
PID_FILE="${REPO_DIR}/data/hbc-bot.pid"

if command -v systemctl >/dev/null 2>&1 && systemctl --user cat "${UNIT}" >/dev/null 2>&1; then
  systemctl --user is-active --quiet "${UNIT}"
  exit $?
fi

if [[ -f "${PID_FILE}" ]]; then
  PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    echo "running pid ${PID}"
    exit 0
  fi
fi
echo "not running"
exit 1
