# Operations

## Deployment

Fill .env.bot from the example. The QQ bot needs all-message reception and
proactive-message permission in each connected group. Group names and settings
cards use the official group info and custom Markdown button capabilities.
Only outbound HTTP and WebSocket connections are used.

Startup creates missing tables and prints a random superuser claim token.
Claim it privately once. Console access can grant persistent administration.
Restarting issues another token without clearing existing administrators.

The bot discovers groups from received events. For a group it already joined,
request an @mention so the group appears in the private `/设置` group picker.
Select a default group and any server-specific overrides to enable routing.
After a leave event, a configured group stays visible as exited until it
rejoins. Server routes to it are suspended.

Select **默认服务器** in the private settings menu to set the server for
unqualified commands. The selection is saved per AppID and takes effect on
the next command. **自动选择** clears the selection.

Docker stores data under ./data, writable by UID/GID 1000. Deploy the checkout
with docker compose --env-file .env.bot up -d. Set NO_PROXY for a private
mc-admin host if a proxy is configured.

## Database

The default is data/official-bot.db. Use a separate fresh file. Startup does not
alter old schemas or run migrations. Transfer previous data manually later.

Stop the process before copying SQLite files, or use SQLite's online backup API.
WAL and SHM files can contain committed data. Back up before manual maintenance.

users.qq_id is optional manual data; it never participates in identity matching.
OpenID is scoped by AppID. Replacement bot accounts must not inherit identities
from coincidentally matching OpenIDs. Names/cards can assist manual investigation
but do not prove ownership.

## Cursor and delivery

Game-event changes, responses and cursor commit together. Reconnect from the
saved cursor. cursor_too_old reconnects while retaining it; invalid_cursor stops
the consumer with a critical log. Investigate replay retention and back up before
manually changing event_checkpoint. Never clear it automatically.

Transient HTTP/transport delivery failures retry with capped backoff. Permanent
failures remain marked failed. Logs contain safe status/code, not raw responses.
QQ and SQLite cannot commit atomically: a crash after acceptance can duplicate a
message. Event replay cannot create duplicate outbox rows.

QQ tokens refresh before expiry. A 401 refreshes and retries once. Gateway
reconnect resumes its session/sequence, or identifies again after invalidation.
Missed heartbeat acknowledgments reconnect.

## Restarts

Persist a watch before submission. A known HTTP rejection removes it. Transport
failure may mean the acceptance response was lost: retain the watch, report
uncertainty and do not resubmit. Unknown submissions eventually time out.

Poll accepted tasks. Pending/running is not complete. Failed/cancelled tasks
notify failure. Completed tasks need a ready healthy sample newer than ended_at
and at most six seconds old. Missing task records and deadline expiry produce
notices. Group completion notices follow the server's current route. Private
notices reply to the original request. Persist watches and notifications across
process restarts. A new restart uses the configured deadline when submitted.
The poll interval applies when the monitor schedules its next sleep.

## Credentials and diagnosis

Keep .env.bot, databases and captures outside Git and image contexts. Never log
raw QQ frames: message_scene.ext contains an auth token. Only author profiles
are persisted; scene credentials are discarded.

Action logs identify internal users and operations. Outbox rows retain safe
failure descriptions. Do not paste complete environment files or raw frames
into issue reports.
