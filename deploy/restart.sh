#!/usr/bin/env bash
# Restart hbc-bot: prefer systemd --user, else pidfile + nohup.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="hbc-bot.service"
PID_FILE="${REPO_DIR}/data/hbc-bot.pid"
LOG_FILE="${HOME}/logs/hbc-bot.log"

mkdir -p "${REPO_DIR}/data" "${HOME}/logs"

use_systemd() {
  command -v systemctl >/dev/null 2>&1 \
    && systemctl --user cat "${UNIT}" >/dev/null 2>&1
}

if use_systemd; then
  systemctl --user daemon-reload
  systemctl --user restart "${UNIT}"
  systemctl --user --no-pager --full status "${UNIT}" | head -n 12 || true
  exit 0
fi

# --- pidfile fallback (no systemd user unit) ---
if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "stopping pid ${OLD_PID}"
    kill "${OLD_PID}" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "${OLD_PID}" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "${OLD_PID}" 2>/dev/null; then
      kill -9 "${OLD_PID}" 2>/dev/null || true
    fi
  fi
  rm -f "${PID_FILE}"
fi

if [[ ! -x "${REPO_DIR}/.venv/bin/python" ]]; then
  echo "ERROR: missing ${REPO_DIR}/.venv/bin/python — run: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "ERROR: missing ${REPO_DIR}/.env" >&2
  exit 1
fi

cd "${REPO_DIR}"
nohup .venv/bin/python -m hbc_bot >>"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
echo "started pid $(cat "${PID_FILE}") log=${LOG_FILE}"
