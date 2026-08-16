# mc-admin QQ Bot

A durable NoneBot2 bridge between one QQ group and Minecraft servers managed by
mc-admin. The bot never accesses Docker, Minecraft files, logs, or RCON ports
directly. All Minecraft operations use the authenticated mc-admin API.

User-visible QQ and Minecraft messages are Chinese. Source code, logs,
configuration, tests, and documentation are English.

## Features

- `/list`, `/ping`, and `/l` show online servers and players.
- `/say` and `/s` send attributed chat for players bound in-game.
- QQ replies route back to the server associated with the original message.
- `/whitelist`, `/ban`, `/unban`, and `/banlist` use fixed, validated RCON
  commands.
- `/restart` persists completion monitoring across bot restarts.
- `\\` and `、、` in-game prefixes relay chat or manage `bind` state.
- SQLite checkpoints and an outbox provide replayable, at-least-once delivery.
- Database migrations run automatically before background workers start.

## Requirements

- Python 3.13
- uv 0.12 or newer
- mc-admin with the bot integration API described in
  [docs/mc-admin-api.md](docs/mc-admin-api.md)
- A OneBot V11 implementation such as NapCat or Lagrange

## Local Setup

Install the locked environment:

```powershell
uv sync --frozen
```

Create `.env.bot` from `.env.bot.example`, replace every placeholder, then run:

```powershell
uv run mc-admin-qqbot
```

The bot listens on `0.0.0.0:8080` by default. mc-admin may be temporarily
offline during startup; the event consumer and outbox retry in the background.

## OneBot V11

Configure NapCat or Lagrange with a reverse WebSocket connection:

```text
ws://<bot-host>:8080/onebot/v11/ws
```

Set the OneBot access token to the exact value of
`MC_ADMIN_QQBOT_ONEBOT_ACCESS_TOKEN`. The logged-in QQ account must match
`MC_ADMIN_QQBOT_QQ_BOT_ID`; a connection for another account is closed. Enable
event and API traffic on the reverse WebSocket. A forward WebSocket or HTTP
callback is not required.

NapCat and Lagrange configuration field names differ by release. In either
implementation, the required values are the reverse WebSocket URL, access
token, and expected QQ account. Do not expose the listener directly to an
untrusted network.

## Configuration

All variables use the `MC_ADMIN_QQBOT_` prefix and are read from the process
environment or `.env.bot`.

| Variable | Required | Description |
| --- | --- | --- |
| `QQ_BOT_ID` | yes | QQ account used by the OneBot connection |
| `QQ_GROUP_ID` | yes | The only group the bot serves |
| `ONEBOT_ACCESS_TOKEN` | yes | Reverse WebSocket access token |
| `MC_ADMIN_BASE_URL` | yes | mc-admin URL including the final `/api` |
| `MC_ADMIN_MASTER_TOKEN` | yes | mc-admin master bearer token |
| `SUPERUSERS` | no | JSON array of QQ IDs |
| `EXCLUDED_SERVERS` | no | JSON array of case-sensitive server IDs |
| `DEFAULT_SERVER` | no | Default case-sensitive server ID |
| `DATABASE_URL` | no | SQLite async URL; default `sqlite+aiosqlite:///./data/bot.db` |
| `RESTART_TIMEOUT_SECONDS` | no | Restart deadline; default `600` |
| `RESTART_POLL_INTERVAL_SECONDS` | no | Status poll interval; default `5` |
| `HOST` | no | Listener address; default `0.0.0.0` |
| `PORT` | no | Listener port; default `8080` |
| `LOG_LEVEL` | no | NoneBot log level; default `INFO` |

Collection values use JSON syntax, for example `[30000, 30001]` and
`["staging", "benchmark"]`. The default server cannot also be excluded.

When the host uses a system proxy and mc-admin is on a local or private
address, set `NO_PROXY` for that host so HTTP and WebSocket connections bypass
the proxy.

## Database and Recovery

Production startup never calls `metadata.create_all()`. It creates the data
directory and runs `alembic upgrade head` before starting consumers. Migration
IDs must match `YYYYMMDD_short_change`, use underscores, and remain at most 32
characters. The initial revision is `20260729_initial_schema`.

See [docs/operations.md](docs/operations.md) for backups, manual migration
commands, cursor recovery, outbox delivery, and restart-watch behavior.

## Containers

Copy the ignored environment example, replace every deployment-specific value,
then pull and run the published bot image:

```powershell
Copy-Item .env.bot.example .env.bot
docker compose --env-file .env.bot pull
docker compose --env-file .env.bot up -d
```

The committed `compose.yaml` contains only interpolated placeholders and safe
defaults. Compose declares every runtime setting, defaults to
`TZ=Asia/Shanghai`, and bind-mounts `./data` at `/app/data`. The checked-out
migrated `data/bot.db` is therefore used directly. The Compose service does not
deploy QQ or mc-admin; the configured mc-admin hostname must be reachable from
the container.

## Development

Run the complete offline validation suite:

```powershell
uv sync --frozen
uv run ruff check .
uv run pyright
uv run pytest
uv run alembic heads
```

The integration test starts a local FastAPI/Uvicorn mc-admin double and uses
NoneBug for OneBot events. It requires no real QQ account, Minecraft server,
mc-admin process, or Docker daemon.

See [docs/architecture.md](docs/architecture.md) for command ownership,
extension points, and transactional rules for game-side commands.
