# mc-admin API for mc-admin-qqbot

The mc-admin HTTP API surface the bot uses. Part 1 documents existing
endpoints (verified against the mc-admin backend as of June 2026); Part 2
specifies new APIs that must be added to mc-admin for the bot to work.

mc-admin source: `mc-admin/backend` (FastAPI). All routes are mounted under
`/api`.

## Authentication

Two mechanisms exist; the bot uses the **master token**:

- Send `Authorization: Bearer <master_token>` on every HTTP request and on
  WebSocket handshakes. The master token is configured in mc-admin's
  `config.toml` (`master_token`) and bypasses the cookie/CSRF session checks
  entirely (CSRF only applies to cookie-authenticated requests).
- WebSocket auth (`get_websocket_user`) reads the same `Authorization`
  header. The `Origin` check passes when no `Origin` header is sent — fine
  for non-browser clients.
- The browser flow (`POST /api/auth/token` password login → HttpOnly JWT
  cookie + CSRF cookie) is not useful for a headless bot.

The master token is a root credential; the bot must load it from
configuration and never log it. (If finer-grained service credentials are
ever wanted, that's an mc-admin feature request — out of scope here.)

Error shape: all errors, including validation errors, are
`{"detail": "<string>"}` with an appropriate HTTP status.

## Part 1 — existing endpoints

### Health

`GET /api/system/health` → `{"status": "healthy"}` — unauthenticated;
useful as a reachability probe.

### Servers

`GET /api/servers/`
List of all managed servers (basic info only, no runtime status):

```json
[
  {
    "id": "vanilla",
    "name": "vanilla",
    "serverType": "VANILLA",
    "gameVersion": "1.20.4",
    "gamePort": 25565,
    "maxMemoryBytes": 4294967296,
    "rconPort": 25575,
    "javaVersion": 21
  }
]
```

`gamePort` provides the stable ordering the bot uses for listings and
prefix-match priority.

`GET /api/servers/{server_id}` → same shape as one list item; `404` if the
server doesn't exist.

`GET /api/servers/{server_id}/status` → `{"status": "<state>"}` where
`<state>` is one of the hierarchical states
`REMOVED < EXISTS < CREATED < RUNNING < STARTING < HEALTHY`.
For the bot: "online" ≙ `RUNNING`/`STARTING`/`HEALTHY`; "responsive" ≙
`HEALTHY`.

`POST /api/servers/{server_id}/operations` with body
`{"action": "start" | "stop" | "restart" | "up" | "down"}` →
`{"message": "..."}` on success, `404` unknown server, `400` invalid action.
`restart` returns when the container restart has been issued, **not** when
the server is playable — the bot polls `/status` until `HEALTHY` to announce
restart completion. (`remove` also exists; the bot must never send it.)

### Players & sessions

`GET /api/players/?online_only=<bool>&server_id=<id>` →

```json
[
  {
    "player_db_id": 7,
    "uuid": "069a79f444e94726a5befca90e38aaf5",
    "current_name": "Notch",
    "avatar_base64": "…or null",
    "is_online": true,
    "last_seen": "2026-06-12T03:21:00Z",
    "total_playtime_seconds": 12345,
    "first_seen": "2026-01-01T00:00:00Z"
  }
]
```

`GET /api/servers/{server_id}/online-players` →

```json
[
  {
    "player_db_id": 7,
    "uuid": "…",
    "current_name": "Notch",
    "avatar_base64": "…or null",
    "joined_at": "2026-06-12T03:00:00Z",
    "session_duration_seconds": 1260
  }
]
```

Online status is derived from mc-admin's log monitor and is periodically
reconciled against RCON `list`, so it can lag reality by up to one sync
interval. Note: names matching mc-admin's dynamic
`players.ignored_name_prefixes` (default `["bot_"]`, case-insensitive) are
excluded from tracking and will not appear here or in chat history.

For the all-servers listing the bot should prefer the new overview endpoint
(§2.4) over fanning out per-server status + online-players calls.

`GET /api/players/uuid/{uuid}` → player detail:
`{player_db_id, uuid, current_name, skin_base64, avatar_base64, is_online,
current_servers: [server_id], last_seen, first_seen,
total_playtime_seconds}`. `404` if unknown. This is the bot's
binding-display-name lookup.

`GET /api/players/{player_db_id}/chat?limit=&server_id=&search=&start_date=&end_date=`
→ `[{message_id, server_db_id, server_id, message_text, sent_at}]`
(newest-first, `limit` 1–500, default 100). History only — see Part 2 for
the real-time stream.

`GET /api/players/{player_db_id}/sessions`, `…/sessions/stats` — join/leave
history; not needed for the core bot features but available.

### Console WebSocket — do not use

`WS /api/servers/{server_id}/console?cols=&rows=` is a docker-attach
**terminal** bridge (raw TTY bytes, ANSI escapes, shared stdin). It is built
for the web UI's xterm and is unsuitable for programmatic command execution
— there is no request/response correlation. The bot uses the new command
endpoint (Part 2) instead.

## Part 2 — new APIs to implement in mc-admin

Four additions. Shapes below are the contract the bot will code against;
internal implementation is free (mc-admin already has the building blocks:
RCON access via its Docker manager, and the log-monitor → tracking pipeline
that parses chat).

### 2.1 Execute a server command (RCON)

```
POST /api/servers/{server_id}/rcon
Body:     {"command": "whitelist add Notch"}
Response: {"output": "Added Notch to the whitelist"}
```

- Runs the command via RCON and returns the complete textual response
  (empty string when the command produces none).
- `404` if the server doesn't exist; `409` (or similar) if the server isn't
  in a state where RCON is available, with a `detail` the bot can relay.
- A modest server-side timeout that surfaces as an error status rather than
  hanging.
- Auth: same as everything else; this endpoint is effectively op-level
  access, which is acceptable under the master token.

Used by the bot for: `whitelist add/remove/list`, `ban`, `pardon`,
`banlist`.

### 2.2 Send a chat message to a server

```
POST /api/servers/{server_id}/message
Body:     {"message": "*<Alice> hello", "target_player": "Notch" | null,
           "color": "yellow" | null}
Response: {"ok": true}   (or 204)
```

- Displays the message in-game (tellraw-style), to everyone when
  `target_player` is null, otherwise privately to that player.
- mc-admin owns correct escaping/quoting of arbitrary user text and
  multi-line handling — this is the reason this is a separate endpoint
  rather than letting the bot craft `tellraw` JSON through 2.1.
- `color` optional with a sensible default; accept the vanilla Minecraft
  color names.
- Errors as in 2.1. Sending to a player who is offline may be a silent
  no-op (vanilla behavior) — the bot does not depend on delivery receipts
  to individual players.

Used by the bot for: `/say` delivery, reply routing, in-game `\\bind`
responses.

### 2.3 Real-time event stream

A single subscription delivering parsed game events for **all** active
servers, over WebSocket (or SSE — implementer's choice; WebSocket sketched
here):

```
WS /api/events?since=<cursor>
```

Server → client messages, one JSON object per event:

```json
{
  "cursor": "opaque-monotonic-id",
  "type": "chat",
  "server_id": "vanilla",
  "timestamp": "2026-06-12T03:21:00Z",
  "player": {"name": "Notch", "uuid": "069a79f4… or null", "player_db_id": 7},
  "message": "\\anyone up for the dragon?"
}
```

Requirements:

- **`chat` events are required.** `player_join`, `player_leave`, and
  `server_status` event types are welcome additions (same envelope, no
  `message`/different payload) but the bot's current features only need
  chat; the schema must allow unknown `type`s so they can be added without
  breaking clients.
- **Identity included**: each chat event carries the player's resolved
  identity (`uuid`, `player_db_id`) when mc-admin can resolve it, plus the
  raw `name` always. This is what makes in-game `\\bind` trustworthy without
  the bot doing its own identity resolution. `uuid` may be null when
  resolution fails (e.g. just-joined player on a flaky Mojang day).
- **No-loss reconnect**: every event has an opaque, monotonically ordered
  `cursor`. A client reconnecting with `?since=<last-seen-cursor>` receives
  the missed events first (chat is already persisted in
  `player_chat_messages`, so replay can be DB-backed), then the live
  stream. Without `since`, the stream starts at "now". A bounded replay
  window (e.g. whatever is in the DB) is fine; document the behavior when
  the cursor is too old (restart from now + a `gap: true` notice is
  acceptable).
- Events for servers should respect the same `ignored_name_prefixes`
  filtering as chat persistence, so stream and history agree.
- Keepalive: periodic ping/heartbeat so the bot can detect dead
  connections quickly.
- Auth via the `Authorization` header at handshake, as in Part 1.

Used by the bot for: player→QQ relay, in-game `\\bind`, and (if
`server_status` is implemented) restart-completion detection without
polling.

### 2.4 Servers overview

```
GET /api/servers/overview
```

One round-trip for the bot's most-used command (`/list`). Returns every
active server with its status and online players:

```json
[
  {
    "id": "vanilla",
    "name": "vanilla",
    "gamePort": 25565,
    "status": "HEALTHY",
    "online_players": [
      {"name": "Notch", "uuid": "069a79f4…", "player_db_id": 7}
    ]
  },
  {
    "id": "atm9",
    "name": "atm9",
    "gamePort": 25566,
    "status": "STARTING",
    "online_players": []
  }
]
```

- Covers all active servers, including stopped ones (`status` below
  `RUNNING`, `online_players` empty) so the client doesn't need a second
  enumeration call.
- `status` uses the same state names as `GET /servers/{id}/status`.
- `online_players` reflects the same session data as
  `GET /servers/{id}/online-players` (and the same
  `ignored_name_prefixes` filtering); avatars are deliberately omitted to
  keep the payload small.
- A server whose compose/config has drifted and can't be read may be
  omitted or reported with a degraded status — match the behavior of
  `GET /servers/` and document which.
- Likely reusable by the mc-admin web dashboard.

Used by the bot for: `/list`, and as the candidate set for target-server
prefix resolution (it carries `gamePort` for the stable ordering).

### Non-goals for these APIs

- No QQ-specific logic in mc-admin: bindings, group ids, and command
  parsing live in the bot. mc-admin's new surface is generic ("run command",
  "send message", "stream events") and useful beyond this bot.
