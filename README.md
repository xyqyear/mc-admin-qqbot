# mc-admin QQ Bot

A small Python bridge between official QQ group chats and Minecraft servers
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

Fill in .env.bot with the bot's AppID and AppSecret and the mc-admin URL and
token. Enable all group messages, proactive group messages, group info access
and custom Markdown buttons for the official bot.

Every startup prints a new superuser claim token. Send it to the bot **privately**
to claim persistent superuser access. Each token works once; existing superusers
remain authorized. No configured QQ numbers are needed.

## Set up groups

1. Add the bot to each group and @mention it once to make existing groups appear.
2. In a superuser private chat, send `/设置` and select **设置默认群**.
3. Open **服务器群组** to bind a server to a different group or exclude it from
   QQ. Each server has one effective group for both commands and outgoing chat.

Servers without a rule use the default group. **解绑到默认** and **恢复默认**
return a server to default routing. Clearing the default suspends unassigned
servers. Leaving a group suspends any routes to it until it rejoins or a
superuser changes the routes.

Open **系统设置** to edit the restart deadline and status poll interval, with
defaults of 10 minutes and 5 seconds. The bot reads its display name on startup.
Use **刷新机器人名** or `/botname refresh` to fetch it again.

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

Selectors match exact server IDs, then prefixes in game-port order. The
configured default applies in groups that can see it. Otherwise the first
available online server is selected. Current-message group owners or
administrators and persisted superusers can manage visible servers. Private
commands require superuser access. Player confirmation and superuser claiming
are open privately.

QQ's native command panel is configured separately for the group. See
[command panel setup](docs/qq-protocol.md#group-command-panel).

Minecraft messages beginning with `\\` or `、、` are relayed proactively.
Quoting a relay sends a bound user's reply to its original server.

## Configuration

Settings use the MC_ADMIN_QQBOT_ prefix, from environment variables or .env.bot.

| Suffix | Default / meaning |
| --- | --- |
| QQ_APP_ID, QQ_APP_SECRET | Required official bot credentials |
| MC_ADMIN_BASE_URL | Required URL ending in /api |
| MC_ADMIN_MASTER_TOKEN | Required mc-admin bearer token |
| DEFAULT_SERVER | Optional server ID |
| DATABASE_URL | sqlite+aiosqlite:///./data/official-bot.db |
| LOG_LEVEL | INFO |

## Docker

```powershell
docker compose --env-file .env.bot up -d
docker compose logs bot
```

Compose uses `ghcr.io/xyqyear/mc-admin-qqbot:0.2.0` and mounts ./data:/app/data.
The image runs as UID/GID 1000. The mc-admin address must be reachable **from
the container**. For a local source build, tag the image `mc-admin-qqbot:local`
and set `MC_ADMIN_QQBOT_IMAGE` to that tag when running Compose.

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
