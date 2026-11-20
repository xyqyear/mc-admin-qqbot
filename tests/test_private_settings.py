import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from conftest import chat, interaction, make_event, server

from mc_admin_qqbot.mcadmin import McAdminUnavailable, TaskState
from mc_admin_qqbot.models import (
    OutboundDelivery,
    QQGroup,
    ServerGroupRule,
    User,
    utc_now,
)
from mc_admin_qqbot.qq import QQError
from mc_admin_qqbot.workers import check_restart, deliver


async def enroll(app):
    await app.startup()
    await app.handle(make_event(app.claim_token, private=True))


def sent_card(qq):
    return qq.send_card.call_args.args[2]


def card_id(qq):
    return qq.send_card.call_args.kwargs["card_id"]


def card_click(qq, action, **kwargs):
    return interaction(action, button_id=f"{card_id(qq)}-0-0", **kwargs)


async def press(app, qq, label, *, row=None):
    rows = sent_card(qq).rows
    buttons = rows[row] if row is not None else [item for line in rows for item in line]
    chosen = next(item for item in buttons if item.label == label)
    assert not chosen.command
    await app.handle(card_click(qq, chosen.data))
    return sent_card(qq)


def group_event(openid, kind="GROUP_ADD_ROBOT", **data):
    return {"op": 0, "t": kind, "d": {"group_openid": openid, **data}}


async def test_settings_require_current_private_superuser_permission(app, database, qq):
    await app.handle(make_event("/设置", private=True))
    await app.handle(make_event("/设置", role="owner"))
    qq.send_card.assert_not_awaited()
    await enroll(app)
    await app.handle(make_event("/设置", role="owner"))
    qq.send_card.assert_not_awaited()
    await app.handle(make_event("/设置", private=True))
    home = sent_card(qq)
    assert "机器人设置" in home.markdown
    assert "默认群 ｜ 测试群" in home.markdown
    click = card_click(qq, home.rows[1][1].data)
    await app.handle(click)
    assert sent_card(qq).markdown.startswith("设置 › 系统设置")
    await app.handle(click)
    qq.acknowledge.assert_awaited_once_with(click["d"]["id"])

    for denied in (
        card_click(qq, ["save", "timeout", "20"], openid="another-user"),
        card_click(qq, ["save", "timeout", "20"], group="test-group"),
        card_click(qq, "settings:{"),
        card_click(qq, 'settings:{"action":"save"}'),
    ):
        await app.handle(denied)
        qq.acknowledge.assert_awaited_with(denied["d"]["id"], code=4)
    await app.handle(card_click(qq, ["unknown"]))
    assert "此按钮已失效" in sent_card(qq).markdown
    await app.handle(card_click(qq, ["servers", "invalid-page"]))
    assert "此按钮已失效" in sent_card(qq).markdown
    count = qq.acknowledge.await_count
    await app.handle(interaction("unrelated menu event"))
    assert qq.acknowledge.await_count == count

    async with database.sessions.begin() as session:
        user = await session.get(User, 1)
        user.is_superuser = False
    denied = card_click(qq, ["save", "timeout", "20"])
    await app.handle(denied)
    qq.acknowledge.assert_awaited_with(denied["d"]["id"], code=4)
    async with database.sessions() as session:
        assert (await database.preferences(session)).restart_timeout_seconds == 600


async def test_explicit_group_routes_queries_game_chat_and_stale_quotes(
    app, database, qq, mc
):
    await enroll(app)
    async with database.sessions.begin() as session:
        user = await session.get(User, 1)
        user.player_uuid = "player-uuid"
    await app.handle(group_event("creative-group", group_name="创造服"))
    await app.relay.process(chat("、、排队期间改绑", server_id="creative"))
    old_relay = (await database.pending())[-1]
    assert old_relay.destination == "test-group"
    async with database.sessions.begin() as session:
        await session.execute(
            database.route_statement("group", "test-group", "stale", "creative")
        )

    await app.handle(make_event("/设置", private=True))
    await press(app, qq, "服务器群组")
    groups = await press(app, qq, "creative · 绑定")
    assert groups.markdown.startswith("设置 › 服务器群组 › creative › 选择群")
    assert "OpenID …-group" in groups.markdown
    confirmation = await press(app, qq, "创造服")
    assert "creative 已绑定到 创造服" in confirmation.markdown
    await database.close()
    await database.initialize()

    await app.handle(make_event("/list"))
    assert (await database.pending())[-1].body == "没人：[vanilla]"
    await app.handle(make_event("/list", group="creative-group"))
    assert (await database.pending())[-1].body == "没人：[creative]"
    await app.handle(make_event("/whitelist add Steve /creative", role="owner"))
    mc.rcon.assert_not_awaited()
    await app.handle(
        make_event(
            "/whitelist add Steve /creative", role="admin", group="creative-group"
        )
    )
    mc.rcon.assert_awaited_once_with("creative", "whitelist add Steve")
    await app.handle(make_event("旧群引用", ref="stale"))
    mc.send_message.assert_not_awaited()
    assert "在本群不可用" in (await database.pending())[-1].body

    await deliver(old_relay, database, qq, mc)
    qq.send.assert_awaited_once_with(
        "group", "creative-group", old_relay.body, msg_id=None, msg_seq=1
    )
    await app.handle(make_event("新群引用", group="creative-group", ref="REFIDX_sent"))
    mc.send_message.assert_awaited_once_with("creative", "*<NewName> 新群引用")

    await app.relay.process(chat("、、游戏聊天", "2", server_id="creative"))
    pending_relay = (await database.pending())[-1]
    assert pending_relay.destination == "creative-group"
    await app.relay.process(chat("、、不转发", "3", server_id="hidden"))
    assert (await database.pending())[-1].id == pending_relay.id
    await app.relay.process(chat("、、bind", "4", server_id="hidden"))
    assert (await database.pending())[-1].channel == "game"

    await press(app, qq, "排除", row=1)
    await deliver(pending_relay, database, qq, mc)
    assert qq.send.await_count == 1
    async with database.sessions() as session:
        saved = await session.get(OutboundDelivery, pending_relay.id)
        assert saved.status == "failed"
    await app.handle(make_event("/list", group="creative-group"))
    assert "没有服务器" in (await database.pending())[-1].body
    await press(app, qq, "恢复默认", row=1)
    await app.handle(make_event("/list"))
    assert (await database.pending())[-1].body == "没人：[vanilla]、[creative]"

    await press(app, qq, "creative · 绑定")
    await press(app, qq, "创造服")
    reset = await press(app, qq, "解绑到默认")
    assert "creative ｜ 默认群" in reset.markdown


async def test_group_events_refresh_names_and_leave_suspends_routes(
    app, database, qq, mc, monkeypatch
):
    await enroll(app)
    now = utc_now()
    monkeypatch.setattr("mc_admin_qqbot.database.utc_now", lambda: now)
    qq.group_name.side_effect = [QQError(403), "新群名", None]
    processed = asyncio.Queue()

    async def rate_limit(seconds):
        assert seconds == 2
        processed.put_nowait(None)

    with patch("mc_admin_qqbot.application.asyncio.sleep", side_effect=rate_limit):
        worker = asyncio.create_task(app.refresh_group_names())
        try:
            await app.handle(group_event("secondary", "GROUP_MEMBER_ADD"))
            await asyncio.wait_for(processed.get(), 2)
            qq.group_name.assert_awaited_once_with("secondary")
            await app.handle(make_event("普通消息", group="secondary"))
            assert qq.group_name.await_count == 1
            now += timedelta(minutes=10)
            await app.handle(group_event("secondary", "GROUP_OTHER_EVENT"))
            await asyncio.wait_for(processed.get(), 2)
            async with database.sessions() as session:
                group = await session.get(QQGroup, ("test-app", "secondary"))
                assert group.name == "新群名"
                assert group.last_seen_at == now
            await app.handle(group_event("test-group", group_name="测试群"))

            await app.handle(make_event("/设置", private=True))
            await press(app, qq, "服务器群组")
            await press(app, qq, "vanilla · 绑定")
            await press(app, qq, "新群名")
            await app.handle(group_event("secondary", "GROUP_DEL_ROBOT"))
            await app.handle(group_event("secondary", "GROUP_OTHER_EVENT"))
            await app.handle(make_event("/list"))
            assert (await database.pending())[-1].body == "没人：[creative]"
            await app.relay.process(chat("、、不发到默认群"))
            assert not any(
                row.dedupe_key.startswith("game:") for row in await database.pending()
            )
            await press(app, qq, "vanilla · 改绑")
            assert "新群名" not in sent_card(qq).markdown
            await app.handle(card_click(qq, ["bind", "vanilla", "secondary", "0"]))
            assert "机器人已不在该群" in sent_card(qq).markdown

            await app.handle(make_event("重新入群后的消息", group="secondary"))
            await asyncio.wait_for(processed.get(), 2)
            async with database.sessions() as session:
                group = await session.get(QQGroup, ("test-app", "secondary"))
                assert group.joined
                assert group.name == "新群名"
                assert (await database.group_routes(session)).destination(
                    "vanilla"
                ) == "secondary"
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


async def test_settings_presets_custom_refresh_and_restart_watch_apply(
    app, database, qq, mc
):
    await enroll(app)
    await app.handle(make_event("/设置", private=True))
    await press(app, qq, "系统设置")
    edit = await press(app, qq, "修改等待上限")
    assert edit.markdown.startswith("设置 › 系统设置 › 重启等待上限")
    assert edit.rows[0][1].primary
    edit_command = edit.rows[1][0]
    assert edit_command.command
    assert edit_command.data == "/设置 重启等待上限 "
    saved = await press(app, qq, "20 分钟")
    assert "已更新 重启等待上限 · 20 分钟" in saved.markdown
    await app.handle(make_event("/设置 状态查询间隔 0", private=True))
    assert "请输入有效的正整数，单位为秒" in sent_card(qq).markdown
    await app.handle(make_event("/设置 状态查询间隔", private=True))
    assert sent_card(qq).markdown.startswith("设置 › 系统设置 › 状态查询间隔")
    await app.handle(make_event("/设置 状态查询间隔 7", private=True))
    assert "已更新 状态查询间隔 · 7 秒" in sent_card(qq).markdown
    await app.handle(make_event("/设置 无效", private=True))
    assert "请发送 /设置" in (await database.pending())[-1].body
    qq.bot_name.return_value = "新机器人"
    await app.handle(make_event("/botname refresh", private=True))
    assert "机器人名 ｜ 新机器人" in sent_card(qq).markdown
    await app.relay.process(chat("、、bind"))
    assert "新机器人" in (await database.pending())[-1].body
    qq.bot_name.side_effect = QQError(500)
    await press(app, qq, "刷新机器人名")
    assert "暂时无法刷新" in sent_card(qq).markdown
    assert "机器人名 ｜ 新机器人" in sent_card(qq).markdown

    started = utc_now()
    await app.handle(make_event("/restart /vanilla", role="admin"))
    watch = (await database.watches())[0]
    assert timedelta(minutes=20) <= watch.deadline - started < timedelta(minutes=21)
    async with database.sessions() as session:
        assert (await database.preferences(session)).restart_poll_interval_seconds == 7
    await app.handle(group_event("other-group", group_name="新默认群"))
    await press(app, qq, "设置默认群")
    await press(app, qq, "新默认群")
    mc.task.return_value = TaskState(status="failed")
    await check_restart(database, mc, watch)
    assert (await database.pending())[-1].destination == "other-group"


async def test_card_rows_keep_context_when_paging_servers_and_groups(
    app, database, qq, mc
):
    await enroll(app)
    mc.servers.return_value = [server(f"survival{i}", 25565 + i) for i in range(6)]
    for i in range(6):
        await app.handle(group_event(f"group-{i}", group_name="同名群"))
    await app.handle(make_event("/设置", private=True))
    first = await press(app, qq, "服务器群组")
    assert len(first.rows) == 5
    second = await press(app, qq, "下一页")
    assert "服务器群组 2/2" in second.markdown
    picker = await press(app, qq, "survival4 · 绑定")
    assert len(picker.rows) == 5
    await press(app, qq, "下一页")
    await press(app, qq, "上一页")
    await press(app, qq, "下一页")
    confirmed = await press(app, qq, "同名群 …roup-4")
    assert "服务器群组 2/2" in confirmed.markdown
    await press(app, qq, "survival4 · 改绑")
    assert "survival4" in sent_card(qq).markdown
    assert "服务器群组 2/2" in (await press(app, qq, "返回")).markdown
    mc.servers.return_value = []
    await press(app, qq, "返回设置")
    assert "暂无服务器" in (await press(app, qq, "服务器群组")).markdown
    await app.handle(card_click(qq, ["exclude", "survival4", "1"]))
    assert "服务器已不存在" in sent_card(qq).markdown
    await press(app, qq, "设置默认群")
    assert "已清除默认群" in (await press(app, qq, "清除默认群")).markdown
    await app.handle(group_event("nameless", group_name=""))
    for openid in ("test-group", "nameless", *(f"group-{i}" for i in range(6))):
        await app.handle(group_event(openid, "GROUP_DEL_ROBOT"))
    assert "暂无已发现的群" in (await press(app, qq, "设置默认群")).markdown


async def test_settings_errors_keep_interaction_and_reply_paths_available(
    app, database, qq, mc
):
    await enroll(app)
    send_card = qq.send_card.side_effect
    qq.send_card.side_effect = QQError(403)
    await app.handle(make_event("/设置", private=True))
    assert "设置卡片暂时无法显示" in (await database.pending())[-1].body
    qq.send_card.side_effect = send_card
    await app.handle(make_event("/设置", private=True))
    qq.acknowledge.side_effect = QQError(500)
    await app.handle(card_click(qq, ["save", "timeout", "20"]))
    async with database.sessions() as session:
        assert (await database.preferences(session)).restart_timeout_seconds == 600
    qq.acknowledge.side_effect = None
    mc.servers.side_effect = McAdminUnavailable("private detail")
    await app.handle(card_click(qq, ["servers", "0"]))
    assert "mc-admin 暂时不可用" in sent_card(qq).markdown


async def test_default_server_persists_and_respects_group_routing(
    app, database, qq, mc
):
    await enroll(app)
    mc.banned_players.return_value = []
    await app.handle(make_event("/设置", private=True))
    assert "默认服务器 ｜ 自动选择" in sent_card(qq).markdown
    picker = await press(app, qq, "默认服务器")
    assert picker.markdown.startswith("设置 › 默认服务器")
    chosen = await press(app, qq, "creative")
    assert "已更新默认服务器 · creative" in chosen.markdown
    assert any(item.label == "✓ creative" for row in chosen.rows for item in row)

    await database.close()
    await database.initialize()
    await app.handle(make_event("/banlist"))
    mc.banned_players.assert_awaited_with("creative")
    await app.handle(make_event("/banlist /vanilla"))
    mc.banned_players.assert_awaited_with("vanilla")

    creative = server("creative", 25566)
    creative.status = "CREATED"
    mc.overview.side_effect = lambda visible: [
        item for item in (server(), creative) if visible(item.id)
    ]
    mc.banned_players.reset_mock()
    await app.handle(make_event("/banlist"))
    mc.banned_players.assert_not_awaited()
    assert (await database.pending())[-1].body == (
        "默认服务器 [creative] 当前不可用，请在命令末尾指定 /服务器"
    )
    creative.status = "HEALTHY"

    await app.handle(group_event("creative-group", group_name="创造服"))
    async with database.sessions.begin() as session:
        session.add(
            ServerGroupRule(
                app_id=database.app_id,
                server_id="creative",
                group_openid="creative-group",
            )
        )
    await app.handle(make_event("/banlist"))
    mc.banned_players.assert_awaited_with("vanilla")
    await app.handle(make_event("/banlist", group="creative-group"))
    mc.banned_players.assert_awaited_with("creative")
    await app.handle(make_event("/banlist", private=True))
    mc.banned_players.assert_awaited_with("creative")

    await press(app, qq, "自动选择")
    await press(app, qq, "返回设置")
    assert "默认服务器 ｜ 自动选择" in sent_card(qq).markdown
    await app.handle(make_event("/banlist", private=True))
    mc.banned_players.assert_awaited_with("vanilla")


async def test_default_server_paging_and_removed_server(app, qq, mc):
    await enroll(app)
    mc.servers.return_value = [server(f"world{i}", 25565 + i) for i in range(6)]
    await app.handle(make_event("/设置", private=True))
    first = await press(app, qq, "默认服务器")
    assert len(first.rows) == 5
    second = await press(app, qq, "下一页")
    assert "默认服务器 2/2" in second.markdown
    selected = await press(app, qq, "world4")
    assert "默认服务器 2/2" in selected.markdown
    assert "当前 ｜ world4" in selected.markdown
    mc.servers.return_value = []
    await press(app, qq, "world5")
    assert "服务器已不存在" in sent_card(qq).markdown
    assert "暂无服务器" in sent_card(qq).markdown
    assert "当前 ｜ world4" in sent_card(qq).markdown


async def test_custom_submission_replaces_the_quoted_edit_card(app, database, qq):
    await enroll(app)
    await app.handle(make_event("/设置 状态查询间隔", private=True))
    assert sent_card(qq).rows[1][0].command
    first_card = card_id(qq)
    await app.handle(make_event("/设置 状态查询间隔", private=True))
    second_card = card_id(qq)
    qq.recall.assert_not_awaited()

    await app.handle(
        make_event("/设置 状态查询间隔 0", private=True, ref=f"REFIDX_{first_card}")
    )
    assert "请输入有效的正整数" in sent_card(qq).markdown
    qq.recall.assert_awaited_once_with("private", "user-one", f"sent-{first_card}")
    assert [entry[0] for entry in qq.mock_calls[-2:]] == ["send_card", "recall"]
    correction = card_id(qq)

    await app.handle(make_event("/设置 状态查询间隔 9", private=True))
    assert qq.recall.await_count == 1
    await app.handle(
        make_event("/设置 重启等待上限 5", private=True, ref=f"REFIDX_{correction}")
    )
    assert qq.recall.await_count == 1
    await app.handle(
        make_event("/设置 状态查询间隔 7", private=True, ref=f"REFIDX_{second_card}")
    )
    qq.recall.assert_awaited_with("private", "user-one", f"sent-{second_card}")
    assert "已更新 状态查询间隔 · 7 秒" in sent_card(qq).markdown
    async with database.sessions() as session:
        assert (await database.preferences(session)).restart_poll_interval_seconds == 7


async def test_replacing_a_card_waits_for_success_and_consumes_it_once(app, qq):
    await enroll(app)
    await app.handle(make_event("/设置", private=True))
    original = card_id(qq)
    system = sent_card(qq).rows[1][1].data
    send_card = qq.send_card.side_effect
    qq.send_card.side_effect = QQError(500)
    await app.handle(interaction(system, button_id=f"{original}-1-1"))
    qq.recall.assert_not_awaited()

    qq.send_card.side_effect = send_card
    qq.recall.side_effect = QQError(400)
    await app.handle(interaction(system, button_id=f"{original}-1-1"))
    assert "系统设置" in sent_card(qq).markdown
    qq.recall.assert_awaited_once_with("private", "user-one", f"sent-{original}")
    assert [entry[0] for entry in qq.mock_calls[-2:]] == ["send_card", "recall"]
    count = qq.send_card.await_count
    stale = interaction(system, button_id=f"{original}-1-1")
    await app.handle(stale)
    qq.acknowledge.assert_awaited_with(stale["d"]["id"], code=3)
    assert qq.send_card.await_count == count
