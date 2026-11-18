#!/usr/bin/env python3
"""Write deploy/*.sh with Unix LF only (run on Windows before commit)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def write(name: str, body: str) -> None:
    path = ROOT / name
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote {path} ({len(text)} bytes)")


AUTO_UPDATE = r'''#!/usr/bin/env bash
# Pull origin/main when it moves, reinstall editable package, restart hbc-bot.
# No sudo. Safe to run from cron every 1–5 minutes.
#
#   deploy/auto-update.sh
#   deploy/auto-update.sh --force   # restart even if SHA unchanged
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${HBC_BOT_BRANCH:-main}"
REMOTE="${HBC_BOT_REMOTE:-origin}"
LOCK_DIR="${REPO_DIR}/data"
LOCK_FILE="${LOCK_DIR}/auto-update.lock"
LOG_PREFIX="[hbc-bot-update]"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
  esac
done

mkdir -p "${LOCK_DIR}"
mkdir -p "${HOME}/logs" 2>/dev/null || true

# flock if available; otherwise a simple mkdir lock
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    echo "${LOG_PREFIX} another update running; exit"
    exit 0
  fi
else
  if ! mkdir "${LOCK_FILE}.d" 2>/dev/null; then
    echo "${LOG_PREFIX} another update running; exit"
    exit 0
  fi
  trap 'rmdir "${LOCK_FILE}.d" 2>/dev/null || true' EXIT
fi

cd "${REPO_DIR}"

if [[ ! -d .git ]]; then
  echo "${LOG_PREFIX} ERROR: ${REPO_DIR} is not a git clone" >&2
  exit 1
fi

if ! git remote get-url "${REMOTE}" >/dev/null 2>&1; then
  echo "${LOG_PREFIX} ERROR: remote '${REMOTE}' missing. Add GitHub remote first." >&2
  exit 1
fi

echo "${LOG_PREFIX} fetch ${REMOTE}/${BRANCH}"
git fetch --quiet "${REMOTE}" "${BRANCH}"

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "${REMOTE}/${BRANCH}")"

CHANGED=0
if [[ "${LOCAL_SHA}" != "${REMOTE_SHA}" ]]; then
  CHANGED=1
  echo "${LOG_PREFIX} update ${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}"
  # Refuse dirty trees so we never clobber vosslink-only edits (.env stays untracked).
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "${LOG_PREFIX} ERROR: working tree dirty (tracked files). Fix or stash on vosslink." >&2
    git status --porcelain --untracked-files=no >&2
    exit 1
  fi
  git merge --ff-only "${REMOTE}/${BRANCH}"
else
  echo "${LOG_PREFIX} already at ${LOCAL_SHA:0:7}"
fi

if [[ "${CHANGED}" -eq 1 ]]; then
  if [[ ! -x .venv/bin/python ]]; then
    echo "${LOG_PREFIX} create .venv"
    python3 -m venv .venv
  fi
  echo "${LOG_PREFIX} pip install -e ."
  .venv/bin/pip install -q -e .
fi

if [[ "${CHANGED}" -eq 1 || "${FORCE}" -eq 1 ]]; then
  echo "${LOG_PREFIX} restart"
  "${REPO_DIR}/deploy/restart.sh"
elif ! "${REPO_DIR}/deploy/status.sh" >/dev/null 2>&1; then
  echo "${LOG_PREFIX} bot not running; start"
  "${REPO_DIR}/deploy/restart.sh"
else
  echo "${LOG_PREFIX} running; no restart"
fi
'''

RESTART = r'''#!/usr/bin/env bash
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
'''

STATUS = r'''#!/usr/bin/env bash
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
'''

STOP = r'''#!/usr/bin/env bash
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
'''

CRONTAB = r'''# Add to vosslab-svc crontab (no sudo). Example:
#   crontab -e
# or append via: (crontab -l 2>/dev/null; cat ~/hbc-bot/deploy/crontab.example | grep -v '^#') | crontab -
#
# Every 2 minutes: fetch main, ff-only pull if moved, pip install -e ., restart.
*/2 * * * * /home/vosslab-svc/hbc-bot/deploy/auto-update.sh >> /home/vosslab-svc/logs/hbc-bot-update.log 2>&1
'''

README_DEPLOY = r'''# Deploy (vosslink, no sudo)

Clone lives at `~/hbc-bot` as `vosslab-svc`. Secrets stay in gitignored `.env`.

## One-time setup

```bash
# 1) Clone (after GitHub remote exists)
cd ~
git clone git@github.com:bahaaaldin214/hbc-bot.git   # or HTTPS
cd hbc-bot
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env   # fill DISCORD_TOKEN, HBC_GUILD_ID, allow-lists

# 2a) Preferred: systemd user unit (restart without sudo)
mkdir -p ~/.config/systemd/user
cp deploy/hbc-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hbc-bot
# Optional once (may need admin): keep unit after logout
#   loginctl enable-linger "$USER"

# 2b) Or skip systemd — auto-update.sh uses pidfile + nohup fallback

# 3) Cron auto-deploy (pull main + relaunch when SHA changes)
mkdir -p ~/logs
crontab -e
# paste the line from deploy/crontab.example
```

## Day to day

Merge/push to `main` on GitHub. Within ~2 minutes cron runs `deploy/auto-update.sh`:

1. `git fetch` + compare SHA to `origin/main`
2. `git merge --ff-only` if moved (aborts if tracked files dirty)
3. `.venv/bin/pip install -e .`
4. `deploy/restart.sh` (`systemctl --user restart` or pidfile relaunch)

Manual:

```bash
~/hbc-bot/deploy/auto-update.sh          # normal
~/hbc-bot/deploy/auto-update.sh --force  # restart even if SHA same
~/hbc-bot/deploy/status.sh
~/hbc-bot/deploy/stop.sh
tail -f ~/logs/hbc-bot-update.log
```

## Notes

- No GitHub Actions runner required on vosslink; cron polls git.
- Private repo: `vosslab-svc` needs read access (deploy key or HTTPS credential).
- Running jobs die on restart (see README “Not built yet”).
'''


def main() -> None:
    write("auto-update.sh", AUTO_UPDATE)
    write("restart.sh", RESTART)
    write("status.sh", STATUS)
    write("stop.sh", STOP)
    write("crontab.example", CRONTAB)
    write("README.md", README_DEPLOY)
    for name in ("auto-update.sh", "restart.sh", "status.sh", "stop.sh"):
        try:
            mode = (ROOT / name).stat().st_mode
            (ROOT / name).chmod(mode | 0o111)
        except OSError:
            pass


if __name__ == "__main__":
    main()
