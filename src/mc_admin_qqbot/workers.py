import asyncio
import logging

import aiohttp
from sqlalchemy import delete

from .config import Settings
from .database import Database
from .mcadmin import (
    ChatEvent,
    McAdminClient,
    McAdminError,
    McAdminResponseError,
    StreamResetFrame,
)
from .models import OutboundDelivery, RestartWatch, utc_now
from .qq import QQClient, QQError
from .relay import GameRelay

log = logging.getLogger(__name__)


async def consume_game(mc: McAdminClient, database: Database, relay: GameRelay) -> None:
    delay = 1
    while True:
        try:
            async for frame in mc.events(await database.checkpoint()):
                delay = 1
                if isinstance(frame, ChatEvent):
                    await relay.process(frame)
                elif isinstance(frame, StreamResetFrame):
                    if frame.reason == "invalid_cursor":
                        log.critical(
                            "mc-admin rejected saved cursor; consumption stopped"
                        )
                        return
                    break
        except McAdminError:
            log.warning("mc-admin event stream unavailable; reconnecting")
        await asyncio.sleep(delay)
        delay = min(30, delay * 2)


async def deliver(
    row: OutboundDelivery, database: Database, qq: QQClient, mc: McAdminClient
) -> None:
    try:
        if row.channel == "game":
            assert row.server_id is not None
            assert isinstance(row.body, str)
            await mc.send_message(
                row.server_id, row.body, target_player=row.destination
            )
            await database.delivered(row)
        else:
            sent = await qq.send(
                row.channel,
                row.destination,
                row.body,
                msg_id=row.reply_msg_id,
                msg_seq=row.reply_seq,
            )
            await database.delivered(row, sent.id, sent.ref_idx)
    except QQError as exc:
        await database.failed(row, str(exc), permanent=not exc.retryable)
        log.warning("delivery id=%s QQ HTTP=%s code=%s", row.id, exc.status, exc.code)
    except McAdminResponseError as exc:
        await database.failed(row, str(exc), permanent=exc.status_code < 500)
    except (aiohttp.ClientError, TimeoutError, McAdminError):
        await database.failed(row, "External service unavailable")


async def deliver_pending(database: Database, qq: QQClient, mc: McAdminClient) -> None:
    while True:
        rows = await database.pending()
        for row in rows:
            await deliver(row, database, qq, mc)
        if not rows:
            await asyncio.sleep(0.5)


async def finish_restart(database: Database, watch: RestartWatch, text: str) -> None:
    async with database.sessions.begin() as session:
        await database.enqueue(
            session,
            key=f"restart:{watch.server_id}:{watch.deadline.isoformat()}",
            channel=watch.scope,
            destination=watch.destination,
            body=text,
            reply_msg_id=watch.reply_msg_id if watch.scope == "private" else None,
            reply_seq=2,
        )
        await session.execute(
            delete(RestartWatch).where(RestartWatch.server_id == watch.server_id)
        )
    log.info(
        "restart server=%s user=%s result=%s", watch.server_id, watch.user_id, text
    )


async def check_restart(
    database: Database, mc: McAdminClient, watch: RestartWatch
) -> None:
    if utc_now() >= watch.deadline:
        await finish_restart(
            database, watch, f"[{watch.server_id}] 重启等待超时，尚未确认恢复"
        )
        return
    if watch.task_id is None:
        return
    try:
        task = await mc.task(watch.task_id)
        if task.status in {"failed", "cancelled"}:
            await finish_restart(
                database, watch, f"[{watch.server_id}] 重启任务失败或已取消"
            )
        elif task.status == "completed" and task.ended_at is not None:
            if await mc.status(watch.server_id, after=task.ended_at) == "HEALTHY":
                await finish_restart(database, watch, f"[{watch.server_id}] 重启完成")
    except McAdminResponseError as exc:
        if exc.status_code == 404:
            await finish_restart(
                database, watch, f"[{watch.server_id}] 重启任务记录已过期，无法确认结果"
            )
    except McAdminError:
        return


async def watch_restarts(
    settings: Settings, database: Database, mc: McAdminClient
) -> None:
    while True:
        for watch in await database.watches():
            await check_restart(database, mc, watch)
        await asyncio.sleep(settings.restart_poll_interval_seconds)
