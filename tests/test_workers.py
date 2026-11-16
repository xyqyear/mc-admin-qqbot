import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from conftest import chat, message

from mc_admin_qqbot.mcadmin import (
    McAdminResponseError,
    McAdminUnavailable,
    StreamResetFrame,
    TaskState,
)
from mc_admin_qqbot.models import OutboundDelivery, RestartWatch, utc_now
from mc_admin_qqbot.qq import QQError
from mc_admin_qqbot.workers import (
    check_restart,
    consume_game,
    deliver,
    deliver_pending,
    watch_restarts,
)


async def test_relay_checkpoint_outbox_and_quote_survive_reopening(
    app, database, qq, mc
):
    await app.relay.process(chat("普通消息"))
    await app.relay.process(chat("、、ignored", "2", server_id="hidden"))
    await app.relay.process(chat("、、", "3"))
    assert await database.checkpoint() == "3"
    assert await database.pending() == []
    await app.relay.process(chat("、、hello", "4"))
    await app.relay.process(chat("、、hello", "4"))
    await app.relay.process(chat("、、old", "3"))
    rows = await database.pending()
    assert len(rows) == 1
    assert rows[0].body == "[vanilla] <Steve>：hello"
    await database.close()
    await database.initialize()
    row = (await database.pending())[0]
    await deliver(row, database, qq, mc)
    qq.send.assert_awaited_once_with(
        "group", "test-group", "[vanilla] <Steve>：hello", msg_id=None, msg_seq=1
    )
    assert await database.pending() == []
    assert await database.route_for(message("reply", ref="REFIDX_sent")) == "vanilla"
    async with database.sessions() as session:
        sent = await session.get(OutboundDelivery, row.id)
        assert sent.remote_message_id == "sent-id"
        assert sent.status == "sent"
    await app.relay.process(chat(r"\\bind", "5"))
    await deliver((await database.pending())[0], database, qq, mc)
    assert mc.send_message.call_args.kwargs["target_player"] == "Steve"


@pytest.mark.parametrize(
    ("channel", "error", "status"),
    [
        ("group", QQError(429, 22009), "pending"),
        ("group", QQError(403, 112), "failed"),
        ("group", aiohttp.ClientConnectionError("ticket"), "pending"),
        ("group", TimeoutError(), "pending"),
        ("game", McAdminResponseError(403), "failed"),
        ("game", McAdminResponseError(503), "pending"),
        ("game", McAdminUnavailable("ticket"), "pending"),
    ],
)
async def test_delivery_failure_policy_and_safe_diagnostics(
    database, qq, mc, channel, error, status
):
    async with database.sessions.begin() as session:
        await database.enqueue(
            session,
            key="delivery",
            channel=channel,
            destination="Steve",
            body="hello",
            server_id="vanilla",
        )
    row = (await database.pending())[0]
    (mc.send_message if channel == "game" else qq.send).side_effect = error
    await deliver(row, database, qq, mc)
    async with database.sessions() as session:
        saved = await session.get(OutboundDelivery, row.id)
        assert saved.status == status
        assert saved.attempts == 1
        assert "ticket" not in saved.last_error
        if status == "pending":
            assert saved.available_at > utc_now()
    assert await database.pending() == []


async def test_outbox_retries_after_restart_and_deduplicates(database, qq, mc):
    for _ in range(2):
        async with database.sessions.begin() as session:
            await database.enqueue(
                session,
                key="one",
                channel="private",
                destination="user-one",
                body="hello",
                reply_msg_id="incoming",
            )
    row = (await database.pending())[0]
    qq.send.side_effect = QQError(500)
    await deliver(row, database, qq, mc)
    await database.close()
    await database.initialize()
    async with database.sessions.begin() as session:
        saved = await session.get(OutboundDelivery, row.id)
        saved.available_at = utc_now() - timedelta(seconds=1)
    qq.send.side_effect = None
    await deliver((await database.pending())[0], database, qq, mc)
    assert await database.pending() == []
    qq.send.assert_awaited_with(
        "private", "user-one", "hello", msg_id="incoming", msg_seq=1
    )


async def make_watch(database, **overrides):
    user = await database.observe(message())
    values = {
        "server_id": "vanilla",
        "task_id": "task-one",
        "user_id": user.id,
        "scope": "group",
        "destination": "test-group",
        "reply_msg_id": "request",
        "deadline": utc_now() + timedelta(minutes=1),
    }
    values.update(overrides)
    async with database.sessions.begin() as session:
        session.add(RestartWatch(**values))
    return (await database.watches())[0]


async def test_restart_requires_task_terminal_and_post_completion_health(database, mc):
    watch = await make_watch(database)
    for state in ["pending", "running"]:
        mc.task.return_value = TaskState.model_validate({"status": state})
        await check_restart(database, mc, watch)
        assert await database.watches()
        assert not mc.status.called
        assert await database.pending() == []
    mc.task.return_value = TaskState(status="completed")
    await check_restart(database, mc, watch)
    assert not mc.status.called
    ended = utc_now()
    mc.task.return_value = TaskState(status="completed", ended_at=ended)
    mc.status.side_effect = McAdminUnavailable("old sample")
    await check_restart(database, mc, watch)
    assert await database.watches()
    mc.status.side_effect = None
    mc.status.return_value = "STARTING"
    await check_restart(database, mc, watch)
    assert await database.watches()
    mc.status.return_value = "HEALTHY"
    await check_restart(database, mc, watch)
    mc.status.assert_awaited_with("vanilla", after=ended)
    assert await database.watches() == []
    row = (await database.pending())[0]
    assert row.body == "[vanilla] 重启完成"
    assert row.reply_msg_id is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("failed", "失败"),
        ("cancelled", "取消"),
        ("expired", "过期"),
        ("timeout", "超时"),
    ],
)
async def test_restart_failure_timeout_and_private_notification(
    database, mc, state, expected
):
    watch = await make_watch(database, scope="private", destination="user-one")
    if state == "expired":
        mc.task.side_effect = McAdminResponseError(404)
    elif state == "timeout":
        watch.deadline = utc_now() - timedelta(seconds=1)
    else:
        mc.task.return_value = TaskState(status=state)
    await check_restart(database, mc, watch)
    assert await database.watches() == []
    row = (await database.pending())[0]
    assert expected in row.body
    assert row.reply_msg_id == "request"
    assert row.reply_seq == 2


async def test_restart_unknown_and_temporary_errors_stay_pending(database, mc):
    watch = await make_watch(database, task_id=None)
    await check_restart(database, mc, watch)
    assert not mc.task.called
    watch.task_id = "task-one"
    for error in [McAdminUnavailable("offline"), McAdminResponseError(503)]:
        mc.task.side_effect = error
        await check_restart(database, mc, watch)
        assert await database.watches()
        assert not await database.pending()


async def test_background_workers_cancel_and_close_cleanly(app, database, mc, qq):
    await app.relay.process(chat("、、relay"))
    stopped = asyncio.Event()

    async def pause(_):
        stopped.set()
        await asyncio.Event().wait()

    with patch("mc_admin_qqbot.workers.asyncio.sleep", side_effect=pause):
        task = asyncio.create_task(deliver_pending(database, qq, mc))
        await asyncio.wait_for(stopped.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await database.pending() == []
    watch = await make_watch(database)
    mc.task.return_value = TaskState(status="running")
    stopped.clear()
    with patch("mc_admin_qqbot.workers.asyncio.sleep", side_effect=pause):
        task = asyncio.create_task(watch_restarts(app.settings, database, mc))
        await asyncio.wait_for(stopped.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await database.watches())[0].server_id == watch.server_id


async def test_event_stream_reconnect_keeps_cursor_and_stops_invalid_cursor(
    app, database, mc
):
    calls = []

    async def frames(since):
        calls.append(since)
        match len(calls):
            case 1:
                raise McAdminUnavailable("offline")
            case 2:
                yield StreamResetFrame(type="stream_reset", reason="cursor_too_old")
            case 3:
                yield chat("、、relay", "12")
                yield StreamResetFrame(type="stream_reset", reason="invalid_cursor")

    mc.events = frames
    with patch(
        "mc_admin_qqbot.workers.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await consume_game(mc, database, app.relay)
    assert calls == [None, None, None]
    assert await database.checkpoint() == "12"
    assert len(await database.pending()) == 1
