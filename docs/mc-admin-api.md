# mc-admin API for mc-admin-qqbot

The bot targets mc-admin 7.1's API. All routes below include `/api`.
`McAdminClient` owns wire models and assembles the command-facing server view.
Commands do not parse monitoring responses themselves.

## Authentication

Send `Authorization: Bearer <master_token>` on HTTP requests and the event
WebSocket handshake. Never log the token. Headless WebSocket clients omit
`Origin`. HTTP errors may carry a string or structured `detail` field.

## Server observations

- `GET /api/servers/`: static metadata, including `id`, `name` and `gamePort`.
- `GET /api/monitoring`: one shared observation for all registered servers.
- `GET /api/servers/{server_id}/online-players`: session-derived players with
  `player_db_id`, `uuid` and `current_name`; additional fields are ignored.

The client joins metadata and `monitoring.servers[server_id].status`, then
fetches players only for `RUNNING`, `STARTING` or `HEALTHY` servers. It maps
`current_name` to the command model's `name`. Stopped servers have no players.
Ordering and server selectors remain owned by the command domain.

A status sample has `value`, `quality`, `sampled_at` and `error`. The bot only
uses `ready` samples with a timezone-aware timestamp no older than six seconds.
Missing, pending, stale, unavailable or malformed observations are errors;
they must not be presented as an empty server list or a stopped server.
The bot does not call legacy overview or per-server status routes.

## Restart

`POST /api/servers/{server_id}/operations`, body `{"action":"restart"}`,
returns HTTP 202 with a `task_id`. Acceptance does not mean the server is ready.
The bot persists its watch before submission and removes it if submission fails.

Completion monitoring reads `/api/monitoring` and requires status samples newer
than the watch's creation time. A ready unhealthy-to-healthy transition or the
existing stability window confirms recovery. Unknown observations reset the
healthy streak without recording a server-state transition. See
[operations](operations.md#restart-watches) for timeout and recovery behavior.

## Players and commands

- `GET /api/players/uuid/{uuid}` returns `player_db_id`, `uuid` and
  `current_name` for the binding's display name.
- `POST /api/servers/{server_id}/rcon`, body `{"command":"..."}`, returns
  `{"output":"..."}`. Commands validate their own player names and permissions.
- `POST /api/servers/{server_id}/message` accepts `message`, optional
  `target_player` and `color`. mc-admin escapes the tellraw JSON.
- `GET /api/system/health` returns `{"status":"healthy"}` without authentication.

## Event stream

`WS /api/events?since=<cursor>` streams chat and live events. A chat frame has
`type`, `cursor`, `server_id`, `timestamp`, `player` and `message`. Its player
contains `name`, `uuid` and `player_db_id`. Unknown event types are ignored.

Only chat events are replayable. The cursor advances transactionally with the
bot's handling and outbox records. Reconnect with the last committed cursor;
`cursor_too_old` reconnects without clearing it, while `invalid_cursor` stops
consumption for operator investigation. Event handling and delivery remain
independent of server-list observations.
