#!/usr/bin/env bash
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
