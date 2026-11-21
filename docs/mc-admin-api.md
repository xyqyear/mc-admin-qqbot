# mc-admin API contract

Use the current API under a base URL ending in /api. HTTP and WebSocket handshakes
send Authorization: Bearer <master_token>. Headless WebSockets omit Origin.

| Endpoint | Bot use |
| --- | --- |
| GET /servers/ | Server ID and gamePort |
| GET /monitoring | Shared server status samples and current maintenance |
| GET /servers/{id}/online-players | Player list, only for healthy servers |
| GET /players/uuid/{uuid} | Binding's current player name |
| POST /servers/{id}/message | message, color, optional target_player |
| GET /servers/{id}/files/content?path=/banned-players.json | Structured Java player bans |
| POST /servers/{id}/rcon | Whitelist queries and validated whitelist/ban changes |
| POST /servers/{id}/operations | `start`, `stop` or `restart` action, 202 with task_id |
| GET /tasks/{task_id} | Status and completion time for the `/restart` watch |
| GET /tasks?server_id={id} | Active and recent lifecycle tasks for the card |
| GET /snapshots/restorations/active?server_id={id}&limit=1 | Active restoration and write gate |
| GET /snapshots/restorations?server_id={id}&limit=1 | Latest restoration if none is active |
| WS /events?since=<cursor> | Replayable chat |

Players contain player_db_id, uuid and current_name. Message submission adds
target_player for private binding responses. mc-admin escapes tellraw JSON.
RCON returns {"output":"..."}.

The ban list reply reads the server's JSON file through its file API. Vanilla
RCON can concatenate ban entries without line breaks, including free-form reasons.
The image includes names from permanent and unexpired player bans.

Monitoring reads servers[id].status: value, quality and sampled_at. Require ready
samples, timezone-aware time and age at most six seconds. The management card
shows missing or stale observations as waiting for a status update and disables
lifecycle actions. Active maintenance and restorations also block card actions.
Start and restart need a completed task and a healthy sample newer than its
ended_at to show the game as ready.

Chat frames contain type, cursor, server_id, timestamp, player and message. Player
has name and optional uuid/player_db_id; binding requires UUID. Unknown event
types are ignored. Heartbeats have type/timestamp. Stream reset reason is
cursor_too_old or invalid_cursor. See [operations](operations.md) for recovery.
