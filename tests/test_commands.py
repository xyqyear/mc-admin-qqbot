from io import BytesIO

import pytest
from conftest import make_event, message
from PIL import Image, ImageDraw

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
    await app.handle(make_event("/unknown"))
    await app.handle(make_event("聊天不是命令"))
    assert len(await database.pending()) == 1


async def test_help_and_private_superuser_commands(app, database):
    await bind_user(database, admin=True)
    await app.handle(make_event("/help", private=True))
    help_text = (await database.pending())[-1].body
    assert "/restart /<服务器>" in help_text
    assert "/设置" in help_text
    assert "/botname refresh" in help_text


async def test_group_help_follows_role_in_current_message(app, database):
    await app.handle(make_event("/help", role="owner"))
    owner_help = (await database.pending())[-1].body
    for command in ("/ban <", "/unban <", "/restart /", "/whitelist add/remove"):
        assert command in owner_help
    assert "/设置" not in owner_help

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


@pytest.mark.parametrize(
    ("command", "private", "target", "rcon", "heading"),
    [
        ("/whitelist list /creative", False, "creative", "whitelist list", "白名单"),
        ("/banlist", True, "vanilla", None, "封禁名单"),
    ],
)
async def test_lists_render_all_names_in_one_png(
    app, database, mc, monkeypatch, command, private, target, rcon, heading
):
    names = [f"Player{i:02}" for i in range(45, 0, -1)] + ["alpha", "Player42"]
    if rcon is not None:
        mc.rcon.return_value = f"There are {len(names)} players: " + ", ".join(names)
    else:
        mc.banned_players.return_value = names
    drawn = []
    draw_text = ImageDraw.ImageDraw.text

    def record_text(draw, position, text, *args, **kwargs):
        drawn.append(text)
        draw_text(draw, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    if private:
        await bind_user(database, admin=True)
    event = make_event(command, private=private)
    await app.handle(event)

    if rcon is not None:
        mc.rcon.assert_awaited_once_with(target, rcon)
    else:
        mc.banned_players.assert_awaited_once_with(target)
        mc.rcon.assert_not_awaited()
    (reply,) = await database.pending()
    assert reply.destination == ("user-one" if private else "test-group")
    assert reply.reply_msg_id == event["d"]["id"]
    assert isinstance(reply.body, bytes)
    with Image.open(BytesIO(reply.body)) as image:
        assert image.format == "PNG"
        image.verify()
    assert heading in drawn
    assert [text for text in drawn if text in names] == sorted(names, key=str.casefold)


async def test_empty_list_gets_an_image_and_bad_server_data_gets_a_safe_error(
    app, database, mc
):
    mc.banned_players.return_value = []
    await app.handle(make_event("/banlist"))
    assert (await database.pending())[-1].body.startswith(b"\x89PNG\r\n\x1a\n")
    mc.rcon.return_value = "private server error details"
    await app.handle(make_event("/whitelist list"))
    assert (await database.pending())[-1].body == "服务器返回的白名单无法解析"


@pytest.mark.parametrize(
    ("command", "usage"),
    [("/whitelist list 2 /creative", "/whitelist list"), ("/banlist 1", "/banlist")],
)
async def test_list_arguments_only_accept_the_server_selector(
    app, database, mc, command, usage
):
    await app.handle(make_event(command))
    assert (await database.pending())[-1].body == f"用法：{usage} [/<服务器>]"
    mc.rcon.assert_not_awaited()
    mc.banned_players.assert_not_awaited()


async def test_say_binding_selection_current_name_and_reference_routing(
    app, database, mc
):
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


async def test_unbound_group_chat_uses_card_then_nickname(app, database, mc):
    event = make_event("/s 大家好 /creative", member_nickname="群内的我")
    await app.handle(event)
    mc.send_message.assert_awaited_with("creative", "*<群内的我> 大家好")

    ref = event["d"]["message_scene"]["ext"][0].split("=", 1)[1]
    await app.handle(
        make_event("引用回复 /vanilla", ref=ref, member_nickname="新群名片")
    )
    mc.send_message.assert_awaited_with("creative", "*<新群名片> 引用回复 /vanilla")

    await app.handle(make_event(group="other-group", member_nickname="另一个群的名片"))
    await app.handle(make_event("/say 名片字段缺失", role=None))
    mc.send_message.assert_awaited_with("vanilla", "*<新群名片> 名片字段缺失")

    await app.handle(
        make_event("/say 名片已清空", member_nickname=" ", username="我的昵称")
    )
    mc.send_message.assert_awaited_with("vanilla", "*<我的昵称> 名片已清空")

    await app.handle(
        make_event("/say 暂无资料", openid="new-member", username=None, role=None)
    )
    mc.send_message.assert_awaited_with("vanilla", "*<QQ用户> 暂无资料")
    mc.player.assert_not_awaited()
    assert await database.pending() == []


async def test_private_game_chat_requires_player_binding(app, database, mc):
    app.claim_token = "test-admin-token"
    await app.handle(make_event(app.claim_token, private=True))
    await app.handle(make_event("/say 你好", private=True))
    assert "请先在游戏内" in (await database.pending())[-1].body
    mc.send_message.assert_not_awaited()


async def test_reference_routes_are_scoped_and_excluded_servers_stay_blocked(
    app, database, mc
):
    async with database.sessions.begin() as session:
        await session.execute(
            database.route_statement("group", "test-group", "ref", "hidden")
        )
    await app.handle(make_event("reply", ref="ref"))
    assert "在本群不可用" in (await database.pending())[-1].body
    assert not mc.send_message.called
    assert await database.route_for(message(private=True, ref="ref")) is None
    await app.handle(make_event("/say hello", ref="missing"))
    mc.send_message.assert_awaited_with("vanilla", "*<测试群名片> hello")


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
