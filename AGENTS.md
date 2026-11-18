# AGENTS.md — hbc-bot

See `README.md` for purpose, setup and layout.

## Code style (required)

- Every function / method (including helpers, properties, and Discord command handlers) gets a `"""docstring"""`.
- Keep docstrings short: what it does, non-obvious returns / side effects.
- Add brief `#` comments only for technical or complex bits (Discord token quirks, process trees, locks, allowlist edge cases) — not for obvious lines.
- Prefer clear names over long comments.
- No module-level constants (magic numbers/strings/compiled regexes at import time). Put defaults in `Config.from_env`, use `Enum` for closed sets (e.g. job status), or keep values local to the function that needs them.
- SQL DDL lives under `src/hbc_bot/sql/*.sql` and is applied with `executescript` — do not embed schema strings in Python.
- Slash commands are file-based under `src/hbc_bot/commands/` (same idea as bahaa tools): `foo.py` with `async def main(bot, interaction, …)` → `/foo`; `bar/baz.py` → `/bar baz`. Private helpers are `_*.py`. Optional `option_help()`, `autocomplete_<param>()`, `default_permissions()`. Do not hand-register cog classes in `__main__`.

## Discord-native first (required)

Minimize our logic; maximize Discord's. Before adding a gate, parser, or flow, check whether the platform already does it (slash options, autocomplete, modals with selects / text / file upload, buttons, ephemeral replies, command permissions, contexts). Add bot code only for what Discord cannot express.

- **Who / which channel (lab server):** Discord command permissions. Commands that run things define `default_permissions()` returning `discord.Permissions.none()` (admin-only until granted under Server Settings → Integrations). Do not add user/role/channel allowlists to `.env` or `config.toml`.
- **Where commands exist:** `allowed_contexts` / `allowed_installs` on the bot (`__main__`). One global registration, no guild copy (a copy duplicates every entry on the Integrations page).
- **Bot-side gates (`policy.py`), only these:** trusted server `HBC_GUILD_ID` (command permissions are per server), DM allowlist `HBC_ALLOW_DM` + `HBC_DM_USER_IDS`, tool `config.toml` (surface + host), and button presses (requester-only; buttons are outside command permissions).
- **Collecting args:** `ui/forms.py` maps `args.toml` kinds to modal components. The UI only collects; `toolargs.validate` decides. Never add a free-text args option or parse arguments in a command module.
- **Confirm:** a button (`PromptView`), driven by a `confirm` arg in `args.toml`. Never "type yes".
- **Errors / refusals:** always ephemeral (`_common.deny`). `HBC_PUBLIC_CHANNEL_IDS` only affects informational replies (persistent there, ephemeral elsewhere; a later sticky progress channel reuses it + pin/edit); job embeds are always public.

## Tool contract (config.toml / args.toml)

- Toml in the tools tree is the source of truth; do not embed schemas in Python.
- `src/hbc_bot/toolargs.py` is the single validator for CLI and bot. Keep it stdlib-only with no package-relative imports. After editing it or `toolargs.sh`, run `python -m hbc_bot.toolargs install <tools_dir>` to refresh `tools/_lib/`.
- Fail closed: no `config.toml` → tool not on Discord; no / broken `args.toml` → command not runnable from Discord. Terminal: commands with `args.toml` are validated by `tool.sh` before `run.sh` (exit 2).
- A schema must cover every flag real callers use (cron, agent sessions), or those callers break. Check `tools/cron/jobs.txt` and `tools/agent/sessions/` before adding `args.toml` to a command.
- The tools tree on LSS is live (cron). Test `tool.sh` changes in a copied tree first.

## Rules

- Token and IDs live in `.env` (gitignored). Never commit or print it.
- Never build a shell string from Discord input; argv lists only, names validated against the registry, values validated by `toolargs`.
- Keep `toolargs.py`, `registry.py`, `runner.py`, `jobs.py`, `progress.py` free of discord imports so they stay testable.
- All execution goes through `policy.py` and `JobManager.submit`, including any future LLM path.
- Tests: `.venv/Scripts/python -m pytest` (Windows) / `.venv/bin/python -m pytest`. Discord flows are tested with fake interactions (`tests/test_launch.py`).
- Production host: vosslink, user `vosslab-svc`, clone at `~/hbc-bot`.
- Deploy: systemd user unit `deploy/hbc-bot.service` + cron `deploy/auto-update.sh` (poll `origin/main`, ff-only pull, restart). No sudo. Details: `deploy/README.md`.
- Shell scripts must stay Unix LF (`.gitattributes`); regenerate deploy scripts with `python deploy/_write_deploy_scripts.py` if editing on Windows.
