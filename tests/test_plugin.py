import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import nonebot
import nonebot.adapters.onebot.v11.bot as onebot_bot_module
import pytest
from nonebot.adapters.onebot.v11 import (
    Adapter,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Reply, Sender
from nonebug import App

from mc_admin_qqbot.domain import HELP_TEXT, UserInputError
from mc_admin_qqbot.runtime import get_application
from mc_admin_qqbot.services import ListResponse, SilentResponse, TextResponse


@pytest.fixture
def matchers():
    from mc_admin_qqbot import plugin

    return plugin


def group_event(
    message: str | Message,
    *,
    group_id: int = 20000,
    user_id: int = 12345,
    role: str = "member",
    message_id: int = 10,
) -> GroupMessageEvent:
    msg = message if isinstance(message, Message) else Message(message)
    return GroupMessageEvent(
        time=int(time.time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=message_id,
        message=msg,
        original_message=msg,
        raw_message=str(msg),
        font=0,
        sender=Sender(user_id=user_id, nickname="Tester", role=role),
        to_me=False,
        group_id=group_id,
    )


def private_event(
    message: str,
    *,
    user_id: int,
    message_id: int = 10,
) -> PrivateMessageEvent:
    msg = Message(message)
    return PrivateMessageEvent(
        time=int(time.time()),
        self_id=10000,
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=message_id,
        message=msg,
        original_message=msg,
        raw_message=str(msg),
        font=0,
        sender=Sender(user_id=user_id, nickname="Tester"),
        to_me=True,
    )


def create_bot(ctx) -> Bot:
    adapter = nonebot.get_adapter(Adapter)
    return ctx.create_bot(base=Bot, adapter=adapter, self_id="10000")


async def test_help_in_target_group(app: App, matchers) -> None:
    event = group_event("/help")
    async with app.test_matcher(matchers.help_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(MessageSegment.text(HELP_TEXT)),
            result=None,
            bot=bot,
        )


async def test_other_group_and_unauthorized_private_are_ignored(
    app: App, matchers
) -> None:
    async with app.test_matcher(matchers.help_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, group_event("/help", group_id=99999))
        ctx.should_not_pass_rule()
    async with app.test_matcher(matchers.help_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, private_event("/help", user_id=12345))
        ctx.should_not_pass_rule()


async def test_superuser_private_help(app: App, matchers) -> None:
    event = private_event("/help", user_id=30000)
    async with app.test_matcher(matchers.help_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event, Message(MessageSegment.text(HELP_TEXT)), None, bot=bot
        )


async def test_list_command_uses_service(app: App, monkeypatch, matchers) -> None:
    application = get_application()
    operation = AsyncMock(return_value=TextResponse("服务器列表"))
    monkeypatch.setattr(application.commands.list_servers, "execute", operation)
    event = group_event("/ping")
    async with app.test_matcher(matchers.list_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event, Message(MessageSegment.text("服务器列表")), None, bot=bot
        )
    operation.assert_awaited_once()


async def test_say_success_is_silent(app: App, monkeypatch, matchers) -> None:
    application = get_application()
    operation = AsyncMock(return_value=SilentResponse())
    monkeypatch.setattr(application.commands.say, "execute", operation)
    event = group_event("/s hello /van")
    async with app.test_matcher(matchers.say_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
    assert operation.await_args is not None
    called_context, text = operation.await_args.args
    assert called_context.actor_qq == "12345"
    assert text == "hello /van"


async def test_malformed_command_returns_chinese_error(
    app: App, monkeypatch, matchers
) -> None:
    application = get_application()
    operation = AsyncMock(side_effect=UserInputError("用法：/say <消息>"))
    monkeypatch.setattr(application.commands.say, "execute", operation)
    event = group_event("/say")
    async with app.test_matcher(matchers.say_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(MessageSegment.text("用法：/say <消息>")),
            None,
            bot=bot,
        )


async def test_admin_permissions_are_silent(app: App, monkeypatch, matchers) -> None:
    application = get_application()
    operation = AsyncMock(
        side_effect=[PermissionError, TextResponse("已封禁")]
    )
    monkeypatch.setattr(application.commands.ban, "execute", operation)
    async with app.test_matcher(matchers.ban_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, group_event("/ban Notch", role="member"))
    assert operation.await_args is not None
    assert not operation.await_args.args[0].is_admin

    event = group_event("/ban Notch", role="admin")
    async with app.test_matcher(matchers.ban_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event, Message(MessageSegment.text("已封禁")), None, bot=bot
        )
    assert operation.await_count == 2
    assert operation.await_args.args[0].is_admin
    assert operation.await_args.args[1] == "Notch"


async def test_whitelist_list_is_available_to_member(
    app: App, monkeypatch, matchers
) -> None:
    application = get_application()
    operation = AsyncMock(
        return_value=ListResponse("白名单", ("Alice", "Bob"))
    )
    monkeypatch.setattr(application.commands.whitelist, "execute", operation)
    event = group_event("/whitelist list")
    async with app.test_matcher(matchers.whitelist_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(MessageSegment.text("白名单\nAlice、Bob")),
            None,
            bot=bot,
        )
    assert operation.await_args is not None
    assert not operation.await_args.args[0].is_admin
    assert operation.await_args.args[1] == "list"


async def test_reply_fetches_original_message_and_routes(
    app: App, monkeypatch, matchers
) -> None:
    application = get_application()
    route = AsyncMock(return_value="vanilla")
    say = AsyncMock()
    monkeypatch.setattr(application.repository, "route_for", route)
    monkeypatch.setattr(application.commands.say, "execute", say)
    event = group_event("answer [CQ:at,qq=1]", message_id=11)
    event.reply = Reply(
        time=int(time.time()),
        message_type="group",
        message_id=50,
        real_id=50,
        sender=Sender(user_id=10000, nickname="Bot"),
        message=Message("relay"),
    )
    async with app.test_matcher(matchers.reply_matcher) as ctx:
        bot = create_bot(ctx)
        ctx.receive_event(bot, event)
    assert route.await_args is not None
    assert route.await_args.kwargs["message_id"] == "50"
    assert say.await_args is not None
    called_context, reply_text = say.await_args.args
    assert called_context.message_id == "11"
    assert reply_text == "answer"
    assert say.await_args.kwargs == {"routed_server": "vanilla"}


async def test_onebot_resolves_reply_with_get_msg(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = group_event(
        Message([MessageSegment.reply(50), MessageSegment.text(" answer")])
    )
    dispatched = AsyncMock()
    monkeypatch.setattr(onebot_bot_module, "handle_event", dispatched)
    async with app.test_api() as ctx:
        bot = create_bot(ctx)
        ctx.should_call_api(
            "get_msg",
            {"message_id": 50},
            result={
                "time": int(time.time()),
                "message_type": "group",
                "message_id": 50,
                "real_id": 50,
                "sender": {"user_id": 10000, "nickname": "Bot"},
                "message": Message("relay"),
            },
        )
        await bot.handle_event(event)
    assert event.reply is not None
    assert event.reply.message_id == 50
    assert event.get_message().extract_plain_text() == "answer"
    dispatched.assert_awaited_once_with(bot, event)


async def test_context_helpers_cover_identity_private_and_admin(matchers) -> None:
    wrong_bot = group_event("/help")
    wrong_bot.self_id = 99999
    assert not await matchers.authorized_context(wrong_bot)
    private = private_event("/help", user_id=30000)
    assert await matchers.authorized_context(private)
    assert not await matchers.has_reply(private)
    context = matchers.command_context(private)
    assert context.context_type.value == "private"
    assert context.context_id == "30000"
    assert matchers.is_admin(group_event("/ban", user_id=30000))
    assert not matchers.is_admin(group_event("/ban", role="member"))
    unknown = cast(Any, SimpleNamespace(self_id=10000, user_id=30000))
    assert not await matchers.authorized_context(unknown)


async def test_send_reply_empty_private_and_forwarded_lists(matchers) -> None:
    bot = AsyncMock()
    group = group_event("/banlist")
    await matchers.send_response(bot, group, ListResponse("封禁名单", ()))
    assert str(bot.send.await_args.args[1]) == "封禁名单\n暂无"

    bot.reset_mock()
    private = private_event("/banlist", user_id=30000)
    await matchers.send_response(
        bot,
        private,
        ListResponse("白名单", tuple(str(i) for i in range(21))),
    )
    assert bot.send.await_count == 1
    bot.call_api.assert_not_awaited()

    bot.reset_mock()
    items = tuple(f"Player{i}" for i in range(21))
    await matchers.send_response(bot, group, ListResponse("白名单", items))
    assert bot.call_api.await_args.args == ("send_group_forward_msg",)
    assert len(bot.call_api.await_args.kwargs["messages"]) == 21
    bot.send.assert_not_awaited()


async def test_send_reply_forward_fallback_is_single_message(matchers) -> None:
    bot = AsyncMock()
    bot.call_api.side_effect = RuntimeError("unsupported")
    event = group_event("/whitelist list")
    await matchers.send_response(
        bot,
        event,
        ListResponse("白名单", tuple(f"Player{i}" for i in range(21))),
    )
    assert bot.send.await_count == 1
    assert "不支持合并转发" in str(bot.send.await_args.args[1])


async def test_run_replying_hides_permissions_and_errors(matchers) -> None:
    bot = AsyncMock()
    event = group_event("/whitelist add Notch")
    await matchers.execute_command(
        bot,
        event,
        AsyncMock(side_effect=PermissionError),
        action="test",
    )
    bot.send.assert_not_awaited()
    await matchers.execute_command(
        bot,
        event,
        AsyncMock(side_effect=UserInputError("输入错误")),
        action="test",
    )
    assert str(bot.send.await_args.args[1]) == "输入错误"


async def test_reply_without_route_and_reply_failure(
    monkeypatch: pytest.MonkeyPatch, matchers
) -> None:
    application = get_application()
    bot = AsyncMock()
    no_reply = group_event("text")
    await matchers.handle_reply(bot, no_reply)
    bot.send.assert_not_awaited()

    event = group_event("answer")
    event.reply = Reply(
        time=int(time.time()),
        message_type="group",
        message_id=50,
        real_id=50,
        sender=Sender(user_id=10000),
        message=Message("relay"),
    )
    route = AsyncMock(return_value=None)
    monkeypatch.setattr(application.repository, "route_for", route)
    await matchers.handle_reply(bot, event)
    bot.send.assert_not_awaited()

    route.return_value = "vanilla"
    monkeypatch.setattr(
        application.commands.say,
        "execute",
        AsyncMock(side_effect=UserInputError("你还没有绑定")),
    )
    await matchers.handle_reply(bot, event)
    assert str(bot.send.await_args.args[1]) == "你还没有绑定"


async def test_remaining_command_handlers(
    monkeypatch: pytest.MonkeyPatch, matchers
) -> None:
    application = get_application()
    bot = AsyncMock()
    unban = AsyncMock(side_effect=[PermissionError, TextResponse("已解除")])
    banlist = AsyncMock(return_value=ListResponse("封禁名单", ()))
    restart = AsyncMock(
        side_effect=[PermissionError, TextResponse("已开始重启")]
    )
    monkeypatch.setattr(application.commands.unban, "execute", unban)
    monkeypatch.setattr(application.commands.banlist, "execute", banlist)
    monkeypatch.setattr(application.commands.restart, "execute", restart)

    member = group_event("/unban Notch", role="member")
    await matchers.handle_unban(bot, member, Message("Notch"))
    assert unban.await_count == 1
    admin = group_event("/unban Notch", role="owner")
    await matchers.handle_unban(bot, admin, Message("Notch"))
    assert unban.await_count == 2
    assert unban.await_args is not None
    assert unban.await_args.args[0].is_admin
    assert unban.await_args.args[1] == "Notch"

    await matchers.handle_banlist(bot, member, Message("/van"))
    banlist.assert_awaited_once_with("/van")

    await matchers.handle_restart(bot, member, Message("/van"))
    assert restart.await_count == 1
    await matchers.handle_restart(bot, admin, Message("/van"))
    assert restart.await_count == 2
