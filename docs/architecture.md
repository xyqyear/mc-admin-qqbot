# Architecture

One aiohttp session, an async SQLite engine and four asyncio tasks: QQ receive,
mc-admin event receive, delivery and restart monitoring.

| Module | Responsibility |
| --- | --- |
| qq.py | Tokens, HTTP messages, gateway heartbeat/resume, parsed QQ messages |
| mcadmin.py | mc-admin HTTP/WebSocket wire models and client |
| application.py | Startup, profile refresh, private enrollment, dispatch, lifecycle |
| commands.py, domain.py | QQ commands, permissions, targeting, compact output |
| binding.py, relay.py | Game initiation, confirmation, atomic game-event handling |
| models.py, database.py | Profiles, outbox, routes, cursor, restart watches |
| workers.py | Delivery retry, reconnect and restart completion |
| config.py, __main__.py | Settings and process resources |

A new command needs a branch in Commands.handle, nearby behavior and a test.
There is no registration framework, plugin container or protocol SDK.

## Users

users owns the integer ID, optional manual QQ number, unique player UUID and
persisted superuser flag. qq_identity maps AppID + OpenID to the user and stores
the author profile. group_member stores per-group card and role. Commands check
the current message role rather than authorizing from cached profiles.

## Transactions

Game-event processing commits binding changes, deliveries and checkpoint in one
transaction. Ignore replayed cursors. After delivery, commit the remote ID and
server reference route together.

Code confirmation atomically deletes an unexpired request and sets the UUID.
Conflicts roll back consumption. Unique UUID constraints preserve one-to-one
binding. Serialize claims of the single startup token.

Deduplicate QQ events in memory and reply insertions by persisted keys. Synchronous
external QQ command mutations are not exactly once across process crashes.

## Protocol evidence

[QQ protocol](qq-protocol.md) records actual test-bot observations. Tests reproduce
those structures with substituted IDs and tokens. The end-to-end test runs both
protocol servers, real clients, workers and SQLite. Credentials and raw captures
belong only in ignored local files.
