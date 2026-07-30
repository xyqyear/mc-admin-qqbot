# Operations

## Startup Sequence

The process validates configuration before initializing NoneBot. During the
NoneBot startup hook it creates the SQLite parent directory, verifies that the
Alembic graph has one head, upgrades to that head, and then starts the event
consumer, delivery worker, and restart monitor.

mc-admin and QQ connectivity are not startup gates. Failed external delivery
remains pending and is retried with backoff.

## Migrations

Back up the SQLite database before deploying a release with a new revision.
SQLite online backups or a copy made while the bot is stopped are suitable.

Inspect and apply migrations manually with:

```powershell
uv run alembic heads
uv run alembic current
uv run alembic upgrade head
```

The database directory must already exist for standalone Alembic commands.
Application startup creates it automatically.

Do not edit a deployed revision. Add a revision whose ID matches
`YYYYMMDD_short_change`, contains only lowercase letters, digits, and
underscores after the date, and is no longer than 32 characters. Keep one
Alembic head.

## Event Cursor Recovery

The consumer reconnects with the last transactionally committed cursor. A
chat event, its binding change or outbox record, and the checkpoint update are
committed together.

- `cursor_too_old`: retain the checkpoint and reconnect. mc-admin remains the
  authority for replay availability.
- `invalid_cursor`: stop consumption and emit a critical log. Do not clear the
  checkpoint automatically, because that can silently skip history.

Resolve an invalid cursor by investigating mc-admin retention and the stored
`event_checkpoint` value. Take a database backup before changing the cursor.

## Outbox Delivery

QQ and private Minecraft deliveries remain pending until successful. Each
event-derived delivery has a stable deduplication key, so replaying a committed
cursor does not create another row.

Delivery is at least once. A process crash after QQ accepts a message but
before SQLite records success can produce a duplicate after restart. This
small window is intentional; avoiding lost relays takes priority.

## Restart Watches

Restart requests create a persistent watch before calling mc-admin. If the API
request fails, the watch is removed. Otherwise the monitor resumes after bot
restart and announces either completion or timeout through the outbox.

A server is considered recovered after an unhealthy state followed by
`HEALTHY`, or after a minimum stability window with three consecutive healthy
checks. This prevents the pre-restart healthy state from being reported as a
completed restart.

## Secrets and Networking

Keep `.env.bot` outside images and source control. Tokens are loaded as secret
settings and are never passed to NoneBot initialization, preventing DEBUG
configuration dumps from exposing them.

For a local mc-admin behind a host-wide proxy, configure both `NO_PROXY` and
`no_proxy` with the mc-admin hostname or address. Restrict the OneBot listener
with a firewall or reverse proxy when it is not bound to a trusted network.
