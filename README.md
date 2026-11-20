# mc-admin QQ Bot

A small Python bridge between one official QQ bot group and Minecraft servers
managed by mc-admin. It connects directly to QQ's HTTP API and WebSocket gateway.
All Minecraft operations go through mc-admin.

## Start locally

Requires Python 3.13+, uv and a reachable mc-admin API. Roster rendering uses
Microsoft YaHei and Consolas on Windows. On Debian or Ubuntu, install
`fonts-wqy-zenhei` and `fonts-dejavu-core`. The Docker image includes its fonts.

```powershell
uv sync --frozen
Copy-Item .env.bot.example .env.bot
uv run mc-admin-qqbot
```

Fill in .env.bot before starting. Use the bot's AppID, AppSecret and the target
group's **group_openid** from a received QQ event. Enable all group messages
and proactive group messages for the official bot.

Every startup prints a new superuser claim token. Send it to the bot **privately**
to claim persistent superuser access. Each token works once; existing superusers
remain authorized. No configured QQ numbers are needed.

## Bind a player

1. In Minecraft, type `\\bind` (two backslashes) or `、、bind`.
2. The bot privately sends that player a four-digit code, valid for five minutes.
3. Send only those four digits to the bot in a QQ private chat.

`\\bind get` shows the binding; `\\bind remove` removes it.
No QQ number is requested or inferred. The nullable users.qq_id field is for
manual maintenance only. Identity and group profiles refresh on every received
message, including ordinary chat.

## Commands

| Command | Purpose |
| --- | --- |
| `/list`, `/ping`, `/l` | Servers and online players |
| `/say <text> [/<server>]`, `/s` | Chat as the bound player's current name |
| `/whitelist list [/<server>]` | Full whitelist as one image |
| `/banlist [/<server>]` | Full ban list as one image |
| `/whitelist add/remove <player> [/<server>]` | Manage whitelist; admin only |
| `/ban <player>`, `/unban <player>` | Manage bans; admin only |
| `/restart /<server>` | Restart an explicitly selected server; admin only |
| `/help` | Help for the commands available to the caller |

Selectors match exact server IDs, then prefixes in game-port order. Without a
selector, use the configured default or the first online server. Excluded
servers cannot be selected. Current-message group owners/administrators and
persisted superusers can manage servers. Private commands require superuser
access; player confirmation and superuser claiming are open privately.

QQ's native command panel is configured separately for the group. See
[command panel setup](docs/qq-protocol.md#group-command-panel).

Minecraft messages beginning with `\\` or `、、` are relayed proactively.
Quoting a relay sends a bound user's reply to its original server.

## Configuration

Settings use the MC_ADMIN_QQBOT_ prefix, from environment variables or .env.bot.

| Suffix | Default / meaning |
| --- | --- |
| QQ_APP_ID, QQ_APP_SECRET | Required official bot credentials |
| QQ_GROUP_OPENID | Required target group OpenID |
| QQ_BOT_NAME | 罗伯特; shown in binding instructions |
| MC_ADMIN_BASE_URL | Required URL ending in /api |
| MC_ADMIN_MASTER_TOKEN | Required mc-admin bearer token |
| EXCLUDED_SERVERS | []; JSON array of server IDs |
| DEFAULT_SERVER | Optional server ID |
| DATABASE_URL | sqlite+aiosqlite:///./data/official-bot.db |
| RESTART_TIMEOUT_SECONDS | 600 |
| RESTART_POLL_INTERVAL_SECONDS | 5 |
| LOG_LEVEL | INFO |

## Docker

```powershell
docker compose --env-file .env.bot up -d --build
docker compose logs bot
```

Compose builds the checked-out code and mounts ./data:/app/data. The image runs
as UID/GID 1000. No inbound port or callback is required. The mc-admin address
must be reachable **from the container**.

Startup creates a fresh schema if absent. There are no automatic migrations or
old-database compatibility paths. Keep the previous database and transfer any
desired data manually into a separate new database.

## Development and docs

```powershell
uv run ruff check .
uv run pyright
uv run pytest --no-cov tests/test_accounts.py::test_game_binding_requires_private_code_and_retains_identity
```

Run selected behavior tests locally. CI runs the full offline suite with a 95%
branch-coverage gate and builds the application image. Tests use local HTTP,
WebSocket and SQLite connections without external QQ or Minecraft accounts.

- [Behavior requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Operations and recovery](docs/operations.md)
- [mc-admin API contract](docs/mc-admin-api.md)
- [QQ protocol verified with the test bot](docs/qq-protocol.md)
