import pytest
from conftest import make_event, message

from mc_admin_qqbot.domain import UserInputError
from mc_admin_qqbot.mcadmin import McAdminResponseError, McAdminUnavailable
from mc_admin_qqbot.models import MessageRoute, User


async def bind_user(database, *, admin=False):
    user = await database.observe(message())
    async with database.sessions.begin() as session:
        row = await session.get(User, user.id)
        row.player_uuid = "player-uuid"
        row.is_superuser = admin
    return user.id


@pytest.mark.parametrize("command", ["/list", "/ping", "/l"])
async def test_list_aliases_and_unknown_commands(app, database, mc, command):
    await app.handle(make_event(command))
    assert (await database.pending())[-1].body == "没人：[vanilla]、[creative]"
    assert mc.overview.call_args.args == ({"hidden"},)
    await app.handle(make_event("/unknown"))
    await app.handle(make_event("聊天不是命令"))
    assert len(await database.pending()) == 1


async def test_help_and_private_superuser_commands(app, database):
    await bind_user(database, admin=True)
    await app.handle(make_event("/help", private=True))
    assert "/restart /<服务器>" in (await database.pending())[-1].body


async def test_group_help_follows_role_in_current_message(app, database):
    await app.handle(make_event("/help", role="owner"))
    owner_help = (await database.pending())[-1].body
    for command in ("/ban <", "/unban <", "/restart /", "/whitelist add/remove"):
        assert command in owner_help

    await app.handle(make_event("/help", role=None))
    member_help = (await database.pending())[-1].body
    for command in ("/list", "/say", "/whitelist list", "/banlist"):
        assert command in member_help
    for command in ("/ban <", "/unban <", "/restart /", "/whitelist add/remove"):
        assert command not in member_help


@pytest.mark.parametrize(
    "command",
    [
        "/whitelist add Steve",
        "/whitelist remove Steve",
        "/ban Steve",
        "/unban Steve",
        "/restart /vanilla",
    ],
)
async def test_admin_actions_require_current_event_role(app, database, mc, command):
    await database.observe(message(role="admin"))
    await app.handle(make_event(command, role=None))
    assert not mc.overview.called
    assert not mc.rcon.called
    assert not mc.restart.called
    assert await database.pending() == []
    await app.handle(make_event(command, role="member"))
    assert not mc.overview.called


@pytest.mark.parametrize(
    ("text", "rcon", "reply"),
    [
        ("/whitelist add Steve /creative", "whitelist add Steve", "加入白名单"),
        ("/whitelist remove Steve /creative", "whitelist remove Steve", "移出白名单"),
        ("/ban Steve /creative", "ban Steve", "已封禁"),
        ("/unban Steve /creative", "pardon Steve", "解除"),
    ],
)
async def test_group_admin_and_owner_can_manage_players(
    app, database, mc, text, rcon, reply
):
    await app.handle(make_event(text, role="owner"))
    mc.rcon.assert_awaited_once_with("creative", rcon)
    assert reply in (await database.pending())[-1].body


async def test_private_superuser_can_manage_players(app, database, mc):
    await bind_user(database, admin=True)
    await app.handle(make_event("/ban Steve /creative", private=True))
    mc.rcon.assert_awaited_once_with("creative", "ban Steve")


@pytest.mark.parametrize(
    "text",
    [
        "/whitelist",
        "/whitelist invalid",
        "/whitelist add a;stop",
        "/ban @a",
        "/restart",
        "/restart unexpected /vanilla",
    ],
)
async def test_invalid_admin_inputs_do_not_reach_rcon_or_restart(
    app, database, mc, text
):
    await app.handle(make_event(text, role="admin"))
    assert not mc.rcon.called
    assert not mc.restart.called
    assert await database.pending()


async def test_lists_are_compact_paginated_plain_text(app, database, mc):
    names = [f"Player{i}" for i in range(45)]
    mc.rcon.return_value = "There are 45 players: " + ", ".join(names)
    await app.handle(make_event("/whitelist list /creative"))
    reply = (await database.pending())[-1].body
    assert "第 1/3 页" in reply
    assert "Player19" in reply
    assert "Player20" not in reply
    assert "下一页：/whitelist list 2 /creative" in reply
    await app.handle(make_event("/whitelist list 3 /creative"))
    reply = (await database.pending())[-1].body
    assert "第 3/3 页" in reply
    assert "Player44" in reply
    assert "下一页" not in reply
    mc.rcon.return_value = "There are 1 ban(s):\nSteve was banned by Server: Test"
    await app.handle(make_event("/banlist"))
    assert "封禁名单，共 1 人" in (await database.pending())[-1].body
    mc.rcon.assert_awaited_with("vanilla", "banlist players")
    mc.rcon.return_value = "There are no bans"
    await app.handle(make_event("/banlist"))
    assert "暂无" in (await database.pending())[-1].body
    mc.rcon.return_value = "private server error details"
    await app.handle(make_event("/whitelist list"))
    assert "无法解析" in (await database.pending())[-1].body
    assert "private server" not in (await database.pending())[-1].body


@pytest.mark.parametrize(
    ("page", "expected"), [("abc", "正整数"), ("0", "1 至"), ("3", "1 至")]
)
async def test_bad_pages_return_usage(app, database, mc, page, expected):
    mc.rcon.return_value = "There are 1 players: Steve"
    await app.handle(make_event(f"/whitelist list {page}"))
    assert expected in (await database.pending())[-1].body


async def test_say_binding_selection_current_name_and_reference_routing(
    app, database, mc
):
    await app.handle(make_event("/say hello"))
    assert "请先" in (await database.pending())[-1].body
    assert not mc.send_message.called
    await bind_user(database)
    event = make_event("/s hello /creative")
    await app.handle(event)
    mc.send_message.assert_awaited_with("creative", "*<NewName> hello")
    ref = event["d"]["message_scene"]["ext"][0].split("=", 1)[1]
    await app.handle(make_event("引用回复 /vanilla", ref=ref))
    mc.send_message.assert_awaited_with("creative", "*<NewName> 引用回复 /vanilla")
    async with database.sessions() as session:
        route = await session.get(
            MessageRoute, ("test-app", "group", "test-group", ref)
        )
        assert route.server_id == "creative"
    await app.handle(make_event("/say hello /missing"))
    mc.send_message.assert_awaited_with("vanilla", "*<NewName> hello /missing")
    await app.handle(make_event("/say /creative"))
    assert "消息不能为空" in (await database.pending())[-1].body
    await app.handle(make_event("/say"))
    assert "用法" in (await database.pending())[-1].body
    await database.record_route(message(), "vanilla")
    sparse = message()
    sparse = type(sparse)(**{**sparse.__dict__, "msg_idx": None})
    await database.record_route(sparse, "vanilla")


async def test_reference_routes_are_scoped_and_excluded_servers_stay_blocked(
    app, database, mc
):
    await bind_user(database)
    async with database.sessions.begin() as session:
        await session.execute(
            database.route_statement("group", "test-group", "ref", "hidden")
        )
    await app.handle(make_event("reply", ref="ref"))
    assert "已被排除" in (await database.pending())[-1].body
    assert not mc.send_message.called
    assert await database.route_for(message(private=True, ref="ref")) is None
    await app.handle(make_event("/say hello", ref="missing"))
    mc.send_message.assert_awaited_with("vanilla", "*<NewName> hello")


async def test_restart_accepted_is_not_completed_and_duplicate_is_blocked(
    app, database, mc
):
    await app.handle(make_event("/restart /vanilla", role="admin"))
    assert (await database.pending())[-1].body == "[vanilla] 已开始重启"
    watches = await database.watches()
    assert len(watches) == 1
    assert watches[0].task_id == "restart-task"
    await app.handle(make_event("/restart /vanilla", role="admin"))
    assert "已在重启监控中" in (await database.pending())[-1].body
    mc.restart.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "retained", "expected"),
    [
        (McAdminResponseError(409), False, "暂时不可用"),
        (McAdminUnavailable("secret transport error"), True, "未确认"),
    ],
)
async def test_restart_failures_do_not_retry_uncertain_operations(
    app, database, mc, error, retained, expected
):
    mc.restart.side_effect = error
    await app.handle(make_event("/restart /vanilla", role="admin"))
    watches = await database.watches()
    assert bool(watches) is retained
    if watches:
        assert watches[0].task_id is None
    assert expected in (await database.pending())[-1].body
    mc.restart.assert_awaited_once()


async def test_errors_do_not_expose_protocol_or_server_details(
    app, database, mc, caplog
):
    mc.overview.side_effect = McAdminUnavailable("credentials")
    await app.handle(make_event("/list"))
    assert "暂时不可用" in (await database.pending())[-1].body
    mc.overview.side_effect = RuntimeError("login-ticket")
    await app.handle(make_event("/list"))
    assert "命令执行失败" in (await database.pending())[-1].body
    assert "login-ticket" not in caplog.text
    mc.overview.side_effect = UserInputError("具体输入错误")
    await app.handle(make_event("/list"))
    assert (await database.pending())[-1].body == "具体输入错误"
