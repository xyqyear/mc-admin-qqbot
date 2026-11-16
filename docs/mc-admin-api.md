# mc-admin API contract

Use the current API under a base URL ending in /api. HTTP and WebSocket handshakes
send Authorization: Bearer <master_token>. Headless WebSockets omit Origin.

| Endpoint | Bot use |
| --- | --- |
| GET /servers/ | ID, name, gamePort |
| GET /monitoring | Shared server status samples |
| GET /servers/{id}/online-players | Player list, only for healthy servers |
| GET /players/uuid/{uuid} | Binding's current player name |
| POST /servers/{id}/message | message, color, optional target_player |
| POST /servers/{id}/rcon | Validated fixed whitelist/ban command |
| POST /servers/{id}/operations | {"action":"restart"}, 202 with task_id |
| GET /tasks/{task_id} | status and ended_at |
| WS /events?since=<cursor> | Replayable chat |

Players contain player_db_id, uuid and current_name. Message submission adds
target_player for private binding responses. mc-admin escapes tellraw JSON.
RCON returns {"output":"..."}.

Monitoring reads servers[id].status: value, quality and sampled_at. Require ready
samples, timezone-aware time and age at most six seconds. Missing observations
are errors. Restart additionally needs a completed task and a sample newer than
its ended_at.

Chat frames contain type, cursor, server_id, timestamp, player and message. Player
has name and optional uuid/player_db_id; binding requires UUID. Unknown event
types are ignored. Heartbeats have type/timestamp. Stream reset reason is
cursor_too_old or invalid_cursor. See [operations](operations.md) for recovery.
