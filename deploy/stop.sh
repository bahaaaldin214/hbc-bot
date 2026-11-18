#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="hbc-bot.service"
PID_FILE="${REPO_DIR}/data/hbc-bot.pid"

if command -v systemctl >/dev/null 2>&1 && systemctl --user cat "${UNIT}" >/dev/null 2>&1; then
  systemctl --user stop "${UNIT}"
  echo "stopped systemd --user ${UNIT}"
  exit 0
fi

if [[ -f "${PID_FILE}" ]]; then
  PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    sleep 1
    kill -9 "${PID}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
  echo "stopped pidfile"
  exit 0
fi
echo "already stopped"
