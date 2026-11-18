# Deploy (vosslink, no sudo)

Clone lives at `~/hbc-bot` as `vosslab-svc`. Secrets stay in gitignored `.env`.

## One-time setup

```bash
# 1) Clone (after GitHub remote exists)
cd ~
git clone git@github.com:bahaaaldin214/hbc-bot.git   # or HTTPS
cd hbc-bot
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env   # fill DISCORD_TOKEN, HBC_GUILD_ID, HBC_DM_USER_IDS
# Access in the server is granted in Discord: Server Settings -> Integrations -> hbc-bot

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
