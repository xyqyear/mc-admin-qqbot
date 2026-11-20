# Behavior requirements

Connect official QQ groups to multiple mc-admin servers. All game
interactions use mc-admin's authenticated API. User-facing text is Chinese.
Player chat is text, never an RCON command.

## Identity and authorization

- Index users by an integer ID. Match QQ identities by AppID + OpenID.
- Store username, available avatar, useful author fields, first/last seen times
  and per-group card/role. Refresh ordinary messages too. Missing fields retain
  prior values.
- Keep a nullable manually maintained QQ-number column. Binding never writes it.
- Persist superusers. Each startup prints a new random token that can privately
  enroll one superuser. Prior administrators remain authorized.
- Privileged group commands require a superuser or owner/admin role in that
  actual message. Cached roles must never grant authority.
- Ignore chat from bots. Ordinary private users can submit a binding code or
  claim token. Private commands require superuser access.

## Binding

`\\bind` and `、、bind` initiate binding in Minecraft, without arguments.
Require the player's UUID. Send a private four-digit code, valid five minutes and
consumed once on private confirmation. Reissuing replaces the player's previous
code. Limit confirmation attempts.

One user binds one UUID and vice versa. Resolve the current name through mc-admin
when sending chat. bind get/remove/help work with either game prefix. Unbinding
preserves profiles and administrator rights.

## Commands and routing

Preserve /list (/ping, /l), /say (/s), whitelist management, /ban, /unban, /banlist,
/restart and /help. Mutations require admin access. Whitelist and ban list queries
return one PNG with the server, count and every name sorted case-insensitively.
The image shows an empty state when the server returns an empty list.

Show management commands in /help only to callers authorized to run them.

List visible online servers in game-port order, distinguishing populated,
empty and unresponsive servers. Fetch players only for healthy servers.
Monitoring errors must not masquerade as an empty server list.

Per-server commands accept a final /<id-or-prefix>. Exact matches win before
prefixes. With no selector, use the configured default if visible. Report
unavailability when the visible default is offline. Automatic selection and
groups without access to the default use the first visible online server.
Unmatched administrative selectors fail. Chat may retain unmatched selectors as
literal text. Restart always needs an explicit selector. Validate player names
before forming fixed RCON commands.

Relay prefixed game chat proactively. QQ quotes use the actual reference index
to reach the original server. Require binding for explicit and quoted chat.
Check the server's current route when handling quotes in a group.

A server is visible to and relays into the same single group. The default group
applies to servers without explicit rules. A rule can bind a server to a group
or exclude it. Leaving a group suspends routes to it. Player binding remains
available in Minecraft independently of QQ group routing.

Superusers privately manage routing, the default server and restart timing
through `/设置`.
Settings cards show the page and breadcrumb. Group choices come from received
group events. Refresh each group's name periodically on activity and preserve
its last known name when the lookup fails. Persist settings in SQLite. Read the
bot name at startup and on request.

Send a replacement card before recalling its source in the same conversation.
Apply the callback once per card. A custom button fills the input box and
includes a quote of its source. Replace the source when the submitted command
receives a new card, including a card showing input validation errors.

## Persistence and lifecycle

Commit game-event binding changes, responses/relays and stream cursor atomically.
Persist delivery and reference routes. Retry transient failures; retain permanent
failures for inspection. Delivery is at least once, with the remote-send/database
commit duplicate window documented.

Persist a restart watch before submission. Acceptance is not completion. Announce
success after the task completes and a fresh healthy sample is newer than its
end time. Notify failure/timeout; resume watches after process restart. Never
automatically resubmit a restart whose acceptance is uncertain.

Cancel workers and close HTTP/WebSocket/database resources at shutdown.
Never log credentials or raw frames containing scene auth tokens.

Start with a new schema. Old data transfer is manual. Do not add compatibility
adapters, migration machinery or plugin layers.
