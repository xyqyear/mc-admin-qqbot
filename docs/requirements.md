# Behavior requirements

Connect one configured official QQ group to multiple mc-admin servers. All game
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
- Ignore bots and other groups. Ordinary private users can submit a binding code
  or claim token; private commands otherwise require superuser access.

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
/restart and /help. Mutations require admin access. Public lists show 20 player
names per plain-text page with an explicit next-page command.

List online non-excluded servers in game-port order, distinguishing populated,
empty and unresponsive servers. Fetch players only for healthy servers.
Monitoring errors must not masquerade as an empty server list.

Per-server commands accept a final /<id-or-prefix>; exact matches win before
prefixes. No selector uses the configured default or first online server.
Unmatched administrative selectors fail. Chat may retain unmatched selectors as
literal text. Restart always needs an explicit selector. Validate player names
before forming fixed RCON commands.

Relay prefixed game chat proactively. QQ quotes use the actual reference index
to reach the original server. Require binding for explicit and quoted chat.

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
