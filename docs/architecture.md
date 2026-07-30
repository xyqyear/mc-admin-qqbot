# Architecture

The application separates inbound adapters, business services, durable event
processing, and long-running workers. Business command services do not import
NoneBot or OneBot types.

## QQ Commands

Each QQ command has two matching modules:

- `plugin/commands/<command>.py` declares the NoneBot matcher, converts the
  OneBot event into a `CommandContext`, and renders the response.
- `services/commands/<command>.py` validates authorization and input, calls
  mc-admin or repositories, and returns a typed response.

`services/commands/container.py` constructs the typed command collection used
by `Application`. Shared server selection belongs in
`services/commands/targeting.py`. QQ context rules, response rendering, and
error handling belong in the shared `plugin` modules rather than individual
commands.

To add a QQ command:

1. Add its framework-independent service and direct unit tests.
2. Add its matcher module and NoneBug tests.
3. Add the service to `CommandServices` and its builder.
4. Import the matcher from `plugin/__init__.py` so plugin loading registers it.
5. Add user-facing help text when the command is public.

Authorization must be enforced by the service. Adapter-level checks may avoid
unnecessary work, but they are not a security boundary.

## Game Commands

Game commands arrive as mc-admin `ChatEvent` frames rather than NoneBot events.
The `game_commands` package is their inbound adapter:

- `game_commands/router.py` recognizes registered command names.
- `game_commands/commands/<command>.py` adapts a game event to its business
  service.
- `services/game_commands/<command>.py` implements the command behavior.

The current `bind` command follows this structure. A prefixed message whose
first word is not registered remains ordinary player-to-QQ relay text.

To add a game command:

1. Add its service and transaction-focused unit tests.
2. Add its game-command adapter.
3. Register the adapter in `game_commands/container.py`.
4. Add router and event-processor tests for recognized and fallback input.

Game-command mutations execute with the transaction owned by `EventProcessor`.
The mutation, response outbox record, and event checkpoint must commit or roll
back together.

## Events and Workers

`services/events` owns deterministic event processing and outbox creation.
`workers` owns long-running loops, retries, stream reconnection, delivery, and
restart monitoring. `Application` is the composition root and lifecycle owner.

The NoneBot plugin import path remains `mc_admin_qqbot.plugin`; the package
entry point imports each matcher module explicitly.
