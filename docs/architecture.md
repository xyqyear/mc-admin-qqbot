# Architecture

One aiohttp session, an async SQLite engine and five asyncio tasks for QQ
receive, mc-admin event receive, delivery, restart monitoring and group name
refresh.

| Module | Responsibility |
| --- | --- |
| qq.py | Tokens, HTTP messages, gateway heartbeat/resume, parsed QQ messages |
| mcadmin.py | mc-admin HTTP/WebSocket wire models and client |
| application.py | Startup, profile refresh, private enrollment, dispatch, lifecycle |
| commands.py, domain.py, roster.py | QQ commands, permissions, targeting, roster rendering |
| private_settings.py | Private superuser settings cards and actions |
| management.py | Server management cards, task status and confirmed lifecycle operations |
| cards.py | Active card references and send-before-recall sequencing in QQ conversations |
| binding.py, relay.py | Game initiation, confirmation, atomic game-event handling |
| models.py, database.py | Profiles, group directory, settings, outbox, routes, cursor, restart watches |
| workers.py | Delivery retry, reconnect and restart completion |
| config.py, __main__.py | Settings and process resources |

A new command needs a branch in Commands.handle, nearby behavior and a test.
There is no registration framework, plugin container or protocol SDK.

## Users

users owns the integer ID, optional manual QQ number, unique player UUID and
persisted superuser flag. qq_identity maps AppID + OpenID to the user and stores
the author profile. group_member stores per-group card and role. Commands check
the current message role. Management card navigation in a group requires the
original caller and their latest observed group role or current superuser status.
Lifecycle submissions require current superuser status.

## Transactions

Game-event processing commits binding changes, deliveries and checkpoint in one
transaction. Ignore replayed cursors. After delivery, commit the remote ID and
server reference route together.

Code confirmation atomically deletes an unexpired request and sets the UUID.
Conflicts roll back consumption. Unique UUID constraints preserve one-to-one
binding. Serialize claims of the single startup token.

Deduplicate QQ events in memory and reply insertions by persisted keys. Synchronous
external QQ command mutations are not exactly once across process crashes.

Roster PNGs are stored as SQLite BLOB values in the existing outbox body column.
After a successful send, the delivery record keeps the remote ID and drops its
image bytes.

Group routing resolves a server's explicit rule before consulting the default.
An explicit empty rule means exclusion. A missing rule uses the default. The
group must be marked joined to receive messages or see the server. Delivery
resolves the current destination again before a proactive send.

Card buttons carry a card ID. Card state keeps the sending conversation,
returned message ID and any command prefixes. Native prefill buttons attach a
quote. The submitted command resolves that quote in the same conversation.
Active cards live in memory. Replacement sends the new card first and
consumes the reference before attempting to recall the original.

Management cards bind each callback to the sending conversation, caller and
original button data. They check server routing on each callback. Lifecycle
actions consume the callback before submitting to mc-admin so a missing task
receipt cannot trigger a second submission from that card.

## Protocol evidence

[QQ protocol](qq-protocol.md) records actual test-bot observations. Tests reproduce
those structures with substituted IDs and tokens. The end-to-end test runs both
protocol servers, real clients, workers and SQLite. Credentials and raw captures
belong only in ignored local files.
