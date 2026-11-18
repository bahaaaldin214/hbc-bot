# hbc-bot

Discord front-end for the `bahaa` lab tools. Runs on `vosslink` as `vosslab-svc`.

- `/pipeline command` — opens a native form built from the command's `args.toml`, asks for a confirm button when the schema says so, then one embed updates live (status, progress bar, log tail, Cancel button) with the full log attached at the end.
- `/jobs list | show | cancel` — job history (SQLite).
- `/help [tool]` — tool list / `HELP.md`.
- `/reload` — rescan the tools folder (tools, `config.toml`, `args.toml`).

Tools are discovered from `BAHAA_TOOLS_DIR` (`<tool>/tool.sh`, `<tool>/<command>/run.sh`, `HELP.md`) and always executed as `bash <launcher> <tool> <command> <args…>` with no shell interpolation. A new tool appears after `/reload` with no bot change.

Slash commands are discovered the same way from `src/hbc_bot/commands/`: add `foo.py` with `async def main(bot, interaction, …)` → `/foo` (no cog registration). Subfolder `bar/baz.py` → `/bar baz`.

## Design rule: Discord does it, we don't

Prefer a platform feature over bot logic whenever it covers the need.

| Need | Used | Not built |
|------|------|-----------|
| Pick dry vs apply, study, … | String Select inside a modal (`select` args) | free-text `args=` parsing |
| Text / lists / files | Text Input, paragraph Text Input, File Upload in the same modal | path pasting, custom parsers |
| Confirm a write | Button (red when the schema says `danger`) | "type yes" |
| Who may run / in which channel | Discord command permissions (Integrations page) | bot-side user/role/channel allowlists |
| Where commands exist | command `contexts` (server; bot DM only if `HBC_ALLOW_DM`), guild-install only | per-command DM checks |
| Tool / command names | Autocomplete | — |
| Errors and refusals | Ephemeral replies | public error messages |

Bot-side checks remain only where Discord has no equivalent: the trusted server (`HBC_GUILD_ID` — command permissions are per server, so another server's admins must not count), the DM user allowlist, per-tool surface + host (`config.toml`), and who may press a button (buttons are outside command permissions).

## Tool contract: config.toml + args.toml

Lives in the tools tree, next to the tool. One validator (`src/hbc_bot/toolargs.py`, stdlib only) is used by the bot **and** by the terminal path (`tool.sh` → `_lib/toolargs.sh`), so both follow the same rules. Discord only collects values; the validator decides.

```
tools/<tool>/config.toml           [run] host = vosslink|argon|other   [discord] guild / dm = true|false
tools/<tool>/args.toml             optional specs merged under every command (command wins on same name)
tools/<tool>/<command>/args.toml   command schema
tools/_lib/toolargs.{py,sh}        vendored copy: python -m hbc_bot.toolargs install <tools_dir>
```

Fail closed: no `config.toml` → the tool is not on Discord. No (or broken) `args.toml` → the command cannot be started from Discord. In the terminal a command without `args.toml` runs as before; with one, unknown or invalid args exit 2 before `run.sh`.

`[[arg]]` kinds:

| kind | argv | Discord |
|------|------|---------|
| `select` | `flag` + chosen value, or per-choice `argv = ["--apply", "--land"]` (switch style; the flags present must spell exactly one choice) | String Select |
| `string` | `flag VALUE`, or positional when `flag` is omitted; optional `pattern` | Text Input |
| `string_list` | `flag a,b,c` (`join`, default `,`); `pattern` per item | paragraph Text Input (comma / newline) |
| `file` | `flag PATH`; must exist; optional `extensions` | File Upload, staged under `data/uploads/` |
| `confirm` | nothing | button before start; `when = { mode = ["apply"] }`, `danger = true` |

Common keys: `name`, `label`, `help`, `required`, `default`, `discord = false` (terminal-only). At most 5 Discord-visible args per command (modal limit). Values may not start with `-` or contain control characters. Unknown keys are errors.

Pilot: `tools/pipeline/config.toml` + `tools/pipeline/debrief/args.toml` (mirrored in `tests/fixtures/tools/`).

```bash
python -m hbc_bot.toolargs check <tools_dir>/pipeline            # lint a tool's toml
python -m hbc_bot.toolargs argv <tools_dir>/pipeline debrief -- --apply --land
python -m hbc_bot.toolargs install <tools_dir>                   # after editing toolargs.py / toolargs.sh
```

## Setup

1. Discord Developer Portal → New Application → **Bot** → Reset Token. No privileged intents needed. Keep **Public Bot** off and install to the server only (not as a user app).
2. Invite: OAuth2 → URL Generator → scopes `bot` + `applications.commands`; permissions View Channels, Send Messages, Embed Links, Attach Files, Read Message History.
3. `.env` (see `.env.example`): `DISCORD_TOKEN`, `HBC_GUILD_ID` (required for server use — the bot refuses every other server), optional `HBC_PUBLIC_CHANNEL_IDS` so info replies stay in channel history. For private DMs with the bot set `HBC_DM_USER_IDS` (`HBC_ALLOW_DM=1`).
4. **Grant access in Discord, not in `.env`:** Server Settings → Integrations → hbc-bot. `/pipeline`, `/jobs` and `/reload` register as admin-only (`default_member_permissions = 0`). Add the roles / members that may use each command and restrict the channels there. `/help` is open to everyone.
5. Run:

```bash
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m hbc_bot
```

Service + auto-deploy on vosslink (cron pulls `main`, no sudo): see `deploy/README.md`.

## Progress bars

A tool can print `::progress <0-100> [message]` on stdout; the embed shows it as a bar. Tools that don't are shown with their last output lines.

## Layout

```
src/hbc_bot/
  config.py         env / .env
  toolargs.py       config.toml + args.toml loader, shared validator, CLI (stdlib only)
  toolargs.sh       bash hook for tool.sh (vendored to tools/_lib/ with toolargs.py)
  registry.py       scan bahaa tools tree (+ schemas)
  command_loader.py scan commands/ → slash cmds
  policy.py         bot-side gates Discord cannot express
  runner.py         subprocess, streaming, kill tree
  jobs.py           job model, per-tool queue, SQLite history
  progress.py       ::progress protocol
  ui/               embeds, job/prompt buttons, schema → modal form
  commands/         one file (or group folder) per slash command
  sql/              SQLite schema files
```

`toolargs`, `registry`, `runner`, `jobs`, `progress` do not import discord; test with `pytest` (`pip install -e ".[dev]"`).

## Not built yet

- Jobs surviving a bot restart (currently a restart kills running jobs and marks them interrupted).
- Multi-value `select` (Discord supports it; `string_list` covers lists for now).
- More than 5 form fields per command (would need a second modal page).
- LLM help assistant.
