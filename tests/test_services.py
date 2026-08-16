import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.database import Database
from mc_admin_qqbot.domain import UserInputError
from mc_admin_qqbot.game_commands import build_game_command_router
from mc_admin_qqbot.mcadmin import (
    ChatEvent,
    EventPlayer,
    HeartbeatFrame,
    McAdminClient,
    McAdminResponseError,
    McAdminUnavailable,
    OnlinePlayer,
    PlayerDetail,
    ServerOverview,
    StreamResetFrame,
    UnknownFrame,
)
from mc_admin_qqbot.models import (
    ContextType,
    DeliveryChannel,
    DeliveryStatus,
    OutboundDelivery,
    PlayerBinding,
    RestartWatch,
    utc_now,
)
from mc_admin_qqbot.repositories import Repository
from mc_admin_qqbot.services import (
    CommandContext,
    ListResponse,
    TextResponse,
    build_command_services,
)
from mc_admin_qqbot.services.events import EventProcessor, GameRelayService
from mc_admin_qqbot.workers import DeliveryWorker, EventConsumer, RestartMonitor


def overview(status: str = "HEALTHY") -> list[ServerOverview]:
    return [
        ServerOverview(
            id="vanilla",
            name="vanilla",
            gamePort=25565,
            status=status,
            online_players=[OnlinePlayer(name="Alice", uuid="uuid-a", player_db_id=1)],
        )
    ]


def context(*, admin: bool = True) -> CommandContext:
    return CommandContext(
        bot_self_id="10000",
        context_type=ContextType.GROUP,
        context_id="20000",
        actor_qq="12345",
        message_id="10",
        is_admin=admin,
    )


def event(cursor: str, message: str, uuid: str | None = "uuid-a") -> ChatEvent:
    return ChatEvent(
        type="chat",
        cursor=cursor,
        server_id="vanilla",
        timestamp=datetime.now(UTC),
        player=EventPlayer(name="Alice", uuid=uuid, player_db_id=1),
        message=message,
    )


def event_processor(settings: Settings, repository: Repository) -> EventProcessor:
    return EventProcessor(
        settings,
        repository,
        build_game_command_router(),
        GameRelayService(settings),
    )


@pytest.fixture
def client() -> AsyncMock:
    client = AsyncMock(spec=McAdminClient)
    client.overview.return_value = overview()
    client.player.return_value = PlayerDetail(
        player_db_id=1, uuid="uuid-a", current_name="AliceNew"
    )
    client.rcon.return_value = "ok"
    client.status.return_value = "HEALTHY"
    return client


async def test_say_requires_binding_and_records_route(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    service = build_command_services(settings, client, repository).say
    with pytest.raises(UserInputError, match="没有绑定"):
        await service.execute(context(), "hello")
    async with database.sessions.begin() as session:
        session.add(PlayerBinding(qq_id="12345", player_uuid="uuid-a"))
    await service.execute(context(), "hello /van")
    client.player.assert_awaited_once_with("uuid-a")
    client.send_message.assert_awaited_once_with(
        "vanilla", "*<AliceNew> hello", color="yellow"
    )
    assert (
        await repository.route_for(
            bot_self_id="10000",
            context_type=ContextType.GROUP,
            context_id="20000",
            message_id="10",
        )
        == "vanilla"
    )


async def test_say_validation_and_explicit_reply_route(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    service = build_command_services(settings, client, repository).say
    with pytest.raises(UserInputError, match="用法"):
        await service.execute(context(), "   ")
    async with database.sessions.begin() as session:
        session.add(PlayerBinding(qq_id="12345", player_uuid="uuid-a"))
    await service.execute(context(), " reply text ", routed_server="creative")
    client.overview.assert_not_awaited()
    client.send_message.assert_awaited_once_with(
        "creative", "*<AliceNew> reply text", color="yellow"
    )
    with pytest.raises(UserInputError, match="消息不能为空"):
        await service.execute(context(), "/van")


async def test_command_service_lists_and_admin_commands(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    service = build_command_services(
        settings, client, Repository(database.sessions)
    )
    list_response = await service.list_servers.execute()
    assert "[vanilla]" in list_response.text
    client.rcon.side_effect = [
        "There are 2 whitelisted players: Alice, Bob",
        "Added Notch",
        "Banned Notch",
        "\n".join(
            [
                "There are 2 ban(s):",
                "Alice was banned by Server: Griefing, repeatedly",
                "Bob_2 was banned by Admin: Ignored warning: final",
            ]
        ),
    ]
    whitelist = await service.whitelist.execute(context(admin=False), "list /van")
    assert isinstance(whitelist, ListResponse)
    assert whitelist.items == ("Alice", "Bob")
    with pytest.raises(PermissionError):
        await service.whitelist.execute(context(admin=False), "add Notch /van")
    whitelist_response = await service.whitelist.execute(
        context(), "add Notch /van"
    )
    assert isinstance(whitelist_response, TextResponse)
    assert "加入白名单" in whitelist_response.text
    ban_response = await service.ban.execute(context(), "Notch /van")
    assert "已封禁" in ban_response.text
    banlist_response = await service.banlist.execute("/van")
    assert isinstance(banlist_response, ListResponse)
    assert banlist_response.items == ("Alice", "Bob_2")
    with pytest.raises(UserInputError, match="用法"):
        await service.ban.execute(context(), "")


async def test_admin_command_validation_and_unparsed_results(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    service = build_command_services(
        settings, client, Repository(database.sessions)
    )
    with pytest.raises(UserInputError, match="用法"):
        await service.whitelist.execute(context(), "unknown")
    with pytest.raises(UserInputError, match="用法"):
        await service.whitelist.execute(context(admin=False), "list /van extra")
    with pytest.raises(UserInputError, match="用法"):
        await service.whitelist.execute(context(), "add Notch extra /van")
    with pytest.raises(UserInputError, match="选择器"):
        await service.banlist.execute("unexpected text")
    with pytest.raises(UserInputError, match="找不到服务器"):
        await service.banlist.execute("/missing")
    with pytest.raises(UserInputError, match="用法"):
        await service.unban.execute(context(), "Notch extra /van")

    client.rcon.side_effect = [
        "unstructured",
        "unstructured",
        "Removed Notch",
        "Pardoned Notch",
    ]
    whitelist = await service.whitelist.execute(context(admin=False), "list /van")
    assert isinstance(whitelist, TextResponse)
    assert whitelist.text == "服务器返回的白名单无法解析"
    banlist = await service.banlist.execute("/van")
    assert isinstance(banlist, TextResponse)
    assert banlist.text == "服务器返回的封禁名单无法解析"
    remove = await service.whitelist.execute(context(), "remove Notch /van")
    pardon = await service.unban.execute(context(), "Notch /van")
    assert isinstance(remove, TextResponse)
    assert "移出白名单" in remove.text
    assert "已解除" in pardon.text


async def test_admin_services_reject_members_before_external_calls(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    service = build_command_services(
        settings, client, Repository(database.sessions)
    )
    member = context(admin=False)
    with pytest.raises(PermissionError):
        await service.ban.execute(member, "Notch /van")
    with pytest.raises(PermissionError):
        await service.unban.execute(member, "Notch /van")
    with pytest.raises(PermissionError):
        await service.restart.execute(member, "/van")
    client.overview.assert_not_awaited()
    client.rcon.assert_not_awaited()
    client.restart.assert_not_awaited()


async def test_restart_failure_removes_watch(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    service = build_command_services(settings, client, repository).restart
    client.restart.side_effect = McAdminUnavailable("offline")
    with pytest.raises(McAdminUnavailable):
        await service.execute(context(), "/van")
    assert await repository.restart_watches() == []


async def test_event_processor_checkpoints_relay_and_deduplicates(
    database: Database, settings: Settings
) -> None:
    repository = Repository(database.sessions)
    processor = event_processor(settings, repository)
    await processor.process(event("1", "plain chat"))
    assert await repository.checkpoint("mc-admin-chat") == "1"
    assert await repository.pending_deliveries() == []
    await processor.process(event("2", "\\\\hello [CQ:image,file=x]"))
    await processor.process(event("2", "\\\\hello [CQ:image,file=x]"))
    deliveries = await repository.pending_deliveries()
    assert len(deliveries) == 1
    assert deliveries[0].body.endswith("hello [CQ:image,file=x]")
    assert deliveries[0].channel == DeliveryChannel.QQ_GROUP
    assert await repository.checkpoint("mc-admin-chat") == "2"


async def test_event_processor_binding_flow(
    database: Database, settings: Settings
) -> None:
    repository = Repository(database.sessions)
    processor = event_processor(settings, repository)
    await processor.process(event("1", "\\\\bind 12345"))
    assert (await repository.binding_for_qq("12345")).player_uuid == "uuid-a"  # type: ignore[union-attr]
    await processor.process(event("2", "\\\\bind get"))
    await processor.process(event("3", "\\\\bind remove"))
    assert await repository.binding_for_qq("12345") is None
    deliveries = await repository.pending_deliveries()
    assert [delivery.body for delivery in deliveries] == [
        "绑定成功：QQ 12345",
        "当前绑定 QQ：12345",
        "已解除 QQ 12345 的绑定",
    ]
    await processor.process(event("4", "\\\\bind 12"))
    await processor.process(event("5", "\\\\bind 54321", uuid=None))
    deliveries = await repository.pending_deliveries()
    assert deliveries[-2].body == "QQ 号必须是 5 至 12 位数字"
    assert "无法确认" in deliveries[-1].body


async def test_event_processor_binding_conflicts_and_help(
    database: Database, settings: Settings
) -> None:
    repository = Repository(database.sessions)
    processor = event_processor(settings, repository)
    await processor.process(event("1", "、、bind help"))
    await processor.process(event("2", "\\\\bind get"))
    await processor.process(event("3", "\\\\bind remove"))
    await processor.process(event("4", "\\\\bind 12345"))
    await processor.process(event("5", "\\\\bind 54321"))
    other = event("6", "\\\\bind 12345")
    other.player.uuid = "uuid-b"
    other.player.name = "Bob"
    await processor.process(other)
    await processor.process(event("7", "\\\\"))
    await processor.process(event("8", "、、hello"))
    deliveries = await repository.pending_deliveries(limit=20)
    assert deliveries[0].body.startswith("绑定命令")
    assert deliveries[1].body == "当前没有绑定 QQ"
    assert deliveries[2].body == "当前没有绑定 QQ"
    assert deliveries[4].body == "这个玩家已经绑定了 QQ"
    assert deliveries[5].body == "这个 QQ 已绑定其他玩家"
    assert deliveries[-1].body.endswith("hello")


async def test_excluded_event_only_advances_checkpoint(
    database: Database, settings: Settings
) -> None:
    repository = Repository(database.sessions)
    processor = event_processor(settings, repository)
    hidden = event("8", "\\\\secret")
    hidden.server_id = "hidden"
    await processor.process(hidden)
    assert await repository.checkpoint("mc-admin-chat") == "8"
    assert await repository.pending_deliveries() == []


async def test_delivery_worker_sends_qq_and_minecraft(
    database: Database,
    settings: Settings,
    client: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(database.sessions)
    async with database.sessions.begin() as session:
        session.add_all(
            [
                OutboundDelivery(
                    dedupe_key="qq",
                    channel=DeliveryChannel.QQ_GROUP,
                    bot_self_id="10000",
                    destination_id="20000",
                    body="[vanilla] <Alice>：hi",
                    route_server_id="vanilla",
                    status=DeliveryStatus.PENDING,
                    available_at=utc_now(),
                ),
                OutboundDelivery(
                    dedupe_key="mc",
                    channel=DeliveryChannel.MC_PRIVATE,
                    destination_id="Alice",
                    target_player="Alice",
                    server_id="vanilla",
                    body="绑定成功",
                    status=DeliveryStatus.PENDING,
                    available_at=utc_now(),
                ),
            ]
        )

    fake_bot = AsyncMock()
    fake_bot.call_api.return_value = {"message_id": 99}
    monkeypatch.setattr("nonebot.get_bot", lambda _self_id: fake_bot)
    worker = DeliveryWorker(client, repository, settings.qq_bot_id)
    deliveries = await repository.pending_deliveries()
    assert await worker._deliver(deliveries[0]) == "99"
    await repository.mark_delivery_sent(deliveries[0].id, "99")
    assert await worker._deliver(deliveries[1]) is None
    client.send_message.assert_awaited_with(
        "vanilla", "绑定成功", target_player="Alice", color="yellow"
    )


async def test_delivery_worker_private_and_invalid_payloads(
    settings: Settings, client: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = AsyncMock(spec=Repository)
    fake_bot = AsyncMock()
    fake_bot.call_api.return_value = {"message_id": "88"}
    monkeypatch.setattr("nonebot.get_bot", lambda _self_id: fake_bot)
    worker = DeliveryWorker(client, repository, settings.qq_bot_id)
    private = OutboundDelivery(
        id=1,
        dedupe_key="private",
        channel=DeliveryChannel.QQ_PRIVATE,
        destination_id="30000",
        body="通知",
        status=DeliveryStatus.PENDING,
        available_at=utc_now(),
    )
    assert await worker._deliver(private) == "88"
    assert fake_bot.call_api.await_args.args == ("send_private_msg",)
    assert fake_bot.call_api.await_args.kwargs["user_id"] == 30000
    assert fake_bot.call_api.await_args.kwargs["auto_escape"] is False
    assert str(fake_bot.call_api.await_args.kwargs["message"]) == "通知"

    invalid_mc = OutboundDelivery(
        id=2,
        dedupe_key="invalid-mc",
        channel=DeliveryChannel.MC_PRIVATE,
        destination_id="Alice",
        body="通知",
        status=DeliveryStatus.PENDING,
        available_at=utc_now(),
    )
    with pytest.raises(RuntimeError, match="Invalid Minecraft"):
        await worker._deliver(invalid_mc)
    fake_bot.call_api.return_value = {}
    with pytest.raises(RuntimeError, match="message ID"):
        await worker._deliver(private)


async def test_delivery_worker_run_retries_failures(
    settings: Settings, client: AsyncMock
) -> None:
    delivery = OutboundDelivery(
        id=1,
        dedupe_key="retry",
        channel=DeliveryChannel.QQ_GROUP,
        destination_id="20000",
        body="通知",
        status=DeliveryStatus.PENDING,
        available_at=utc_now(),
    )
    repository = AsyncMock(spec=Repository)
    repository.pending_deliveries.side_effect = [[delivery], asyncio.CancelledError]
    worker = DeliveryWorker(client, repository, settings.qq_bot_id)
    worker._deliver = AsyncMock(side_effect=RuntimeError("offline"))  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    repository.mark_delivery_failed.assert_awaited_once_with(1, "offline")


async def test_delivery_worker_run_marks_success_and_propagates_cancel(
    settings: Settings, client: AsyncMock
) -> None:
    delivery = OutboundDelivery(
        id=1,
        dedupe_key="success",
        channel=DeliveryChannel.QQ_GROUP,
        destination_id="20000",
        body="通知",
        status=DeliveryStatus.PENDING,
        available_at=utc_now(),
    )
    repository = AsyncMock(spec=Repository)
    repository.pending_deliveries.side_effect = [[delivery], asyncio.CancelledError]
    worker = DeliveryWorker(client, repository, settings.qq_bot_id)
    worker._deliver = AsyncMock(return_value="99")  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    repository.mark_delivery_sent.assert_awaited_once_with(1, "99")

    repository.reset_mock()
    repository.pending_deliveries.return_value = [delivery]
    repository.pending_deliveries.side_effect = None
    worker._deliver = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    repository.mark_delivery_failed.assert_not_awaited()


async def test_delivery_worker_waits_when_queue_is_empty(
    settings: Settings, client: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = AsyncMock(spec=Repository)
    repository.pending_deliveries.return_value = []
    monkeypatch.setattr(
        "mc_admin_qqbot.workers.delivery.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    with pytest.raises(asyncio.CancelledError):
        await DeliveryWorker(client, repository, settings.qq_bot_id).run()


async def test_restart_command_and_monitor_complete(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    commands = build_command_services(settings, client, repository)
    reply = await commands.restart.execute(context(), "/van")
    assert reply.text == "[vanilla] 已开始重启"
    assert len(await repository.restart_watches()) == 1
    monitor = RestartMonitor(settings, client, repository)
    client.status.return_value = "STARTING"
    watch = (await repository.restart_watches())[0]
    await monitor._check(watch)
    client.status.return_value = "HEALTHY"
    watch = (await repository.restart_watches())[0]
    await monitor._check(watch)
    assert await repository.restart_watches() == []
    delivery = (await repository.pending_deliveries())[0]
    assert delivery.body == "[vanilla] 重启完成"


async def test_restart_timeout_and_duplicate(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    service = build_command_services(settings, client, repository).restart
    await service.execute(context(), "/van")
    with pytest.raises(UserInputError, match="重启监控"):
        await service.execute(context(), "/van")
    async with database.sessions.begin() as session:
        watch = await session.get(RestartWatch, "vanilla")
        assert watch is not None
        watch.deadline = utc_now() - timedelta(seconds=1)
    monitor = RestartMonitor(settings, client, repository)
    await monitor._check((await repository.restart_watches())[0])
    assert (await repository.pending_deliveries())[0].body.endswith("等待超时")


async def test_restart_monitor_stable_window_and_private_notification(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    watch = RestartWatch(
        server_id="vanilla",
        requested_by_qq="30000",
        bot_self_id="10000",
        context_type=ContextType.PRIVATE,
        context_id="30000",
        deadline=utc_now() + timedelta(minutes=10),
        observed_unhealthy=False,
        healthy_streak=2,
        created_at=utc_now() - timedelta(seconds=16),
    )
    assert await repository.add_restart_watch(watch)
    await RestartMonitor(settings, client, repository)._check(
        (await repository.restart_watches())[0]
    )
    delivery = (await repository.pending_deliveries())[0]
    assert delivery.channel == DeliveryChannel.QQ_PRIVATE
    assert delivery.body.endswith("重启完成")


async def test_restart_monitor_treats_mcadmin_error_as_unhealthy(
    database: Database, settings: Settings, client: AsyncMock
) -> None:
    repository = Repository(database.sessions)
    watch = RestartWatch(
        server_id="vanilla",
        requested_by_qq="30000",
        bot_self_id="10000",
        context_type=ContextType.GROUP,
        context_id="20000",
        deadline=utc_now() + timedelta(minutes=10),
        observed_unhealthy=False,
        healthy_streak=2,
    )
    assert await repository.add_restart_watch(watch)
    client.status.side_effect = McAdminUnavailable("offline")
    await RestartMonitor(settings, client, repository)._check(watch)
    current = (await repository.restart_watches())[0]
    assert current.observed_unhealthy
    assert current.healthy_streak == 0


async def test_restart_monitor_run_checks_persisted_watches(
    settings: Settings, client: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = RestartWatch(
        server_id="vanilla",
        requested_by_qq="30000",
        bot_self_id="10000",
        context_type=ContextType.GROUP,
        context_id="20000",
        deadline=utc_now() + timedelta(minutes=10),
        observed_unhealthy=False,
        healthy_streak=0,
    )
    repository = AsyncMock(spec=Repository)
    repository.restart_watches.return_value = [watch]
    monkeypatch.setattr(
        "mc_admin_qqbot.workers.restart_monitor.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    monitor = RestartMonitor(settings, client, repository)
    monitor._check = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await monitor.run()
    monitor._check.assert_awaited_once_with(watch)


async def test_event_consumer_stops_on_invalid_cursor(database: Database) -> None:
    repository = Repository(database.sessions)

    class InvalidCursorClient:
        async def events(self, since: str | None) -> AsyncIterator[StreamResetFrame]:
            yield StreamResetFrame(type="stream_reset", reason="invalid_cursor")

    processor = AsyncMock()
    consumer = EventConsumer(InvalidCursorClient(), repository, processor)  # type: ignore[arg-type]
    await consumer.run()
    processor.process.assert_not_awaited()


async def test_event_consumer_handles_frames_and_cursor_reset(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Repository(database.sessions)
    calls: list[str | None] = []

    class SequenceClient:
        async def events(self, since: str | None):
            calls.append(since)
            if len(calls) == 1:
                yield HeartbeatFrame(type="heartbeat", timestamp=datetime.now(UTC))
                yield UnknownFrame(type="player_join")
                yield event("1", "plain")
                yield StreamResetFrame(type="stream_reset", reason="cursor_too_old")
            else:
                yield StreamResetFrame(type="stream_reset", reason="invalid_cursor")

    monkeypatch.setattr(
        "mc_admin_qqbot.workers.event_consumer.asyncio.sleep", AsyncMock()
    )
    processor = AsyncMock()
    consumer = EventConsumer(SequenceClient(), repository, processor)  # type: ignore[arg-type]
    await consumer.run()
    assert calls == [None, None]
    processor.process.assert_awaited_once()


async def test_event_consumer_retries_mcadmin_errors(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Repository(database.sessions)

    class OfflineClient:
        async def events(self, since: str | None):
            if False:
                yield None
            raise McAdminUnavailable("offline")

    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("mc_admin_qqbot.workers.event_consumer.asyncio.sleep", sleep)
    consumer = EventConsumer(OfflineClient(), repository, AsyncMock())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    sleep.assert_awaited_once()


async def test_event_consumer_propagates_cancellation(database: Database) -> None:
    repository = Repository(database.sessions)

    class CancelledClient:
        async def events(self, since: str | None):
            if False:
                yield None
            raise asyncio.CancelledError

    consumer = EventConsumer(CancelledClient(), repository, AsyncMock())  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()


def test_user_facing_errors_hide_internal_details() -> None:
    from mc_admin_qqbot.plugin.execution import user_facing_error

    assert user_facing_error(UserInputError("输入错误")) == "输入错误"
    assert user_facing_error(McAdminResponseError(500, "raw server detail")) == (
        "mc-admin 暂时不可用，请稍后再试"
    )
    assert user_facing_error(RuntimeError("internal secret")) == (
        "命令执行失败，请稍后再试"
    )
