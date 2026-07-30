# mc-admin-qqbot — Requirements

This document specifies *what* the bot must do, not *how*. Implementation
choices (framework, storage, protocol details, process model) are entirely up
to the implementer, except for the hard constraints in §2. A companion
document, `mc-admin-api.md`, describes the mc-admin HTTP API surface the bot
is expected to build on.

This project replaces `mc-qqbot-next`, a QQ group bot that bridged one QQ
group with a fleet of Minecraft servers running in Docker. The legacy bot
managed the Docker servers directly; this rewrite must not.

## 1. Purpose

A QQ bot that connects **one QQ group** with **multiple Minecraft servers**
managed by an mc-admin instance. It lets group members:

- see which servers are up and who is playing,
- chat with players in-game (both directions),
- and lets trusted members administrate servers (whitelist, bans, restarts)
  without leaving QQ.

## 2. Hard constraints

1. **All Minecraft interaction goes through the mc-admin API.** The bot must
   not touch Docker, server files, server logs, or RCON ports itself. If a
   needed capability is missing from mc-admin, it is added to mc-admin as an
   API (see `mc-admin-api.md` §"New APIs") rather than worked around in the
   bot.
2. The bot serves exactly **one configured QQ group**. Additionally,
   **bot-superusers** (§3) may use every command in **private chat** with the
   bot, for debugging. Messages from any other group, or private messages
   from anyone else, are ignored entirely — no replies, no side effects.
3. All user-facing text (bot replies in QQ, messages shown to players
   in-game) is **Chinese**. Code, comments, logs, and documentation are
   English.
4. Player chat must never be executed, interpreted as markup, or otherwise
   able to break formatting/injection boundaries on either side (QQ message
   escaping, in-game text formatting).

## 3. Concepts

- **Server**: a Minecraft server known to mc-admin, identified by a short
  string id (e.g. `vanilla`, `atm9`). mc-admin reports a lifecycle status for
  each; for the bot's purposes a server is *online* when it is running, and
  *responsive* when mc-admin reports it healthy / can query it.
- **Excluded servers**: a bot-side configured set of server ids the bot
  pretends do not exist — hidden from listings and not addressable by any
  command.
- **Binding**: a persistent association between a QQ account and a Minecraft
  player identity. Bindings are keyed by the player's stable identity (not
  the display name, which can change); the bot displays the current name.
  One QQ account binds at most one player, and one player binds at most one
  QQ account.
- **Admin**: a group member allowed to run privileged commands. At minimum:
  the group owner, group administrators, and a configurable list of
  **bot-superuser** QQ ids. Everyone else is a regular member. Bot-superusers
  are additionally the only accounts allowed to talk to the bot in private
  chat (§2), where they have full admin-level access (group roles don't
  exist in private chat).

## 4. Configuration

Operator-provided configuration must cover at least:

- QQ bot connection credentials (whatever the chosen QQ integration needs).
- The target QQ group id.
- mc-admin base URL and API credential.
- Bot-superuser QQ ids.
- Excluded server ids (possibly empty).
- Default target server (optional; see §6). When unset, the bot picks a
  sensible stable default among online servers (the legacy bot used "lowest
  game port", which operators relied on — keep an equivalent notion of "the
  primary server").
- Restart completion wait limit (default on the order of 10 minutes).

Bad or missing required configuration should fail fast at startup with a
clear error, not at first use.

## 5. QQ-side commands

Commands are messages starting with a command prefix (legacy used `/`).
Aliases shown in parentheses must work. Commands work in the configured
group (this is a deliberate change: the legacy bot accidentally answered
some commands anywhere) and, for bot-superusers only, in private chat with
the bot — every command behaves the same there, with admin-only commands
allowed (this is the debugging channel from §2).

Unless stated otherwise, commands are available to all group members.

### 5.1 `/list` (`/ping`, `/l`) — server & player overview

Shows, in one message:

- every online, non-excluded server and its current player list,
- servers that are online but empty, grouped compactly,
- servers that are online but unresponsive (can't be queried right now),
  marked distinctly,
- or, when nothing is online, a friendly "no servers running" notice.

Ordering must be stable and meaningful across calls (legacy: ascending game
port). Reference output shape (wording may be improved):

```
[vanilla]: Alice, Bob
[creative]: Carol
没人: [atm9], [ftb]
无响应: [broken]
```

### 5.2 `/say <message>` (`/s`) — QQ → game chat

Sends `<message>` to the target server's in-game chat (target selection:
§6), visibly attributed to the sender's bound player name and visually
distinguishable from native in-game chat (legacy rendered
`*<PlayerName> message` in a distinct color).

- Requires the sender to have a binding. If unbound, reply with a hint that
  explains how to bind from in-game (§7.2).
- If delivery to the target server fails, tell the sender which server
  failed. Silence on success is acceptable (legacy behavior) — a delivery
  receipt (e.g. lightweight reaction) is a welcome improvement but not
  required.

### 5.3 Reply routing — answering a server by replying

Every bot-relayed message that originates from a specific server (§7.1) and
every accepted `/say` is remembered together with its server, so that:

- when a group member **replies** (QQ reply feature) to such a message, the
  reply text is sent to that same server as if it were `/say <text> /<that
  server>` — no command needed.
- Replies to bot messages with no recorded server association are ignored.
- The unbound-sender rule of §5.2 applies.
- The same mechanism works in a bot-superuser's private chat for messages
  there that have a recorded server association (e.g. replying to one's own
  earlier `/say`).

This is the primary conversational flow ("someone in-game asked something →
reply to the relayed message") and must feel seamless.

### 5.4 `/whitelist` — whitelist management

- `/whitelist add <player>` — **admin only**. Adds the player to the target
  server's whitelist; relays the server's outcome message.
- `/whitelist remove <player>` — **admin only**. Same, for removal.
- `/whitelist list` — any member. Shows the target server's whitelist. The
  list can be long; present it so it doesn't flood the group (legacy used a
  collapsed/forwarded message).

### 5.5 `/ban <player>`, `/unban <player>` — **admin only**

Ban / pardon the player on the target server; relay the server's outcome
message.

### 5.6 `/banlist` — any member

Show the target server's ban list, with the same flooding consideration as
`/whitelist list`.

### 5.7 `/restart` — **admin only**

Restarts the target server. Safety requirements:

- The target server **must be explicitly specified** (§6); refuse with an
  explanatory message otherwise. A bare `/restart` must never restart a
  default server.
- Acknowledge immediately that the restart has begun.
- Then watch the server until it is responsive again and announce completion
  ("[server] 重启完成" or similar). If it isn't back within the configured
  wait limit, say so instead of staying silent.

### 5.8 `/help`

New in this rewrite: list the available commands, their permissions, and the
target-server convention (§6). Keep it short enough for one message.

### 5.9 Unknown / malformed input

Messages that are not commands and not replies covered by §5.3 are ignored.
Malformed commands (e.g. `/ban` with no player) should produce a brief usage
hint rather than sending a broken instruction to the server.

## 6. Target-server selection (applies to all per-server commands)

Commands that operate on a single server accept an optional trailing server
selector; legacy syntax was a final token `/<name-or-prefix>`, e.g.
`/say hello everyone /van`. Keep the trailing-selector ergonomics (it
composes naturally with free-text messages); the exact syntax may be
refined.

Resolution rules:

1. The selector matches online, non-excluded servers by exact id first, then
   by id prefix (first match in the stable §5.1 ordering wins).
2. No selector → the configured default server, else the "primary" online
   server (§4). If nothing is online, say so.
3. A selector that matches nothing must **not** silently fall back to the
   default server. For chat (`/say`) treating the unmatched token as message
   text is acceptable; for administrative commands (whitelist/ban/restart)
   an unmatched selector is an error and the command must not run. (The
   legacy bot silently fell back, which could aim an admin command at the
   wrong server — this is a deliberate fix.)

## 7. Game-side features

The bot observes in-game chat on all non-excluded servers (via mc-admin) and
reacts as follows. Plain chat that doesn't match the rules below is left
alone — the group is **not** flooded with all in-game chat.

### 7.1 Player → QQ messages

A chat message starting with an escape prefix is relayed to the QQ group.
Legacy prefixes: `\\` and `、、` (the latter exists because Chinese IMEs make
`\` awkward — keep both or equivalents). Example: a player typing
`\\anyone up for the dragon?` produces a group message like:

```
[vanilla] <Alice>: anyone up for the dragon?
```

The relayed group message participates in reply routing (§5.3). Player
message content must be relayed verbatim and safely escaped.

### 7.2 In-game binding management

An escaped message whose body starts with `bind` is a binding command,
answered **privately to that player in-game** (not to all players, not to
the group):

- `\\bind <qq-number>` — create a binding between the typing player and that
  QQ account. Validate the QQ number shape (legacy: 5–12 digits). Reject
  with distinct messages when: the number is invalid, the player is already
  bound, or the QQ account is already bound to another player.
- `\\bind get` — show the player's currently bound QQ, or "not bound".
- `\\bind remove` — remove the player's binding, confirming which QQ was
  unbound.
- `\\bind help`, bare `\\bind` — short in-game usage help.

Binding happens from inside the game on purpose: it proves control of the
player account. Do not add a QQ-side bind command that lets a QQ user claim
an arbitrary player name without verification. An optional improvement is a
QQ-side confirmation step, but in-game initiation must remain the root of
trust.

### 7.3 Player identity freshness

Bound players' display names must stay current when players rename
(mc-admin tracks identity; the bot should rely on it rather than caching
names forever). A renamed player keeps their binding.

## 8. Permissions summary

| Capability                                | Who                          |
| ----------------------------------------- | ---------------------------- |
| `/list`, `/banlist`, `/whitelist list`, `/help` | any group member        |
| `/say`, reply routing                      | any group member with a binding |
| `/whitelist add/remove`, `/ban`, `/unban`, `/restart` | admins (§3)      |
| any command in private chat                | bot-superusers only          |
| in-game `\\bind`, player→QQ relay          | any player on a monitored server |

Permission failures should be silent or minimally noisy (the legacy bot
stayed silent on permission failure to avoid spam; keep that for
non-admins).

## 9. Quality requirements

- **No lost chat**: the player→QQ relay and reply routing must tolerate bot
  or mc-admin restarts and transient disconnects without dropping messages
  sent in the gap, or at minimum recover and clearly resume. Duplicated
  relays should also be avoided.
- **Responsiveness**: relays and command responses should land within a few
  seconds under normal conditions.
- **Resilience**: mc-admin being briefly unreachable must not crash the bot;
  commands during an outage should fail with a clear message, and event
  monitoring should resume automatically.
- **Observability**: log every executed command (who, what, which server)
  and every administrative action's outcome.
- **Tests**: the legacy project had near-complete automated test coverage of
  command behavior; the rewrite should be similarly testable without a real
  QQ connection or a real mc-admin (the spec above is the contract to test
  against).

## 10. Out of scope

- Managing more than one QQ group.
- Creating/deleting servers, file management, backups, maps — that is
  mc-admin's web UI's job.
- Relaying *all* in-game chat to QQ, or QQ chat to game without `/say`.
- Player statistics/playtime reporting (possible future work; mc-admin has
  the data).

## Appendix A — deliberate changes from the legacy bot

For reviewer context; the future implementer can treat the body above as
authoritative.

1. All commands now require the configured group (`/ping`, `/banlist`,
   `/whitelist list`, `/echo` previously answered anywhere), with one new
   sanctioned exception: bot-superusers can run every command in private
   chat as a debugging channel.
2. Unmatched server selectors no longer silently fall back to the default
   server for admin commands (§6.3).
3. `/help` added; legacy `/echo` debug command dropped.
4. Malformed admin commands get usage hints instead of being passed raw to
   the server (§5.9).
5. The bot no longer resolves player identity via Mojang or log parsing —
   identity comes from mc-admin.
