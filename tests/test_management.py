from datetime import timedelta

import pytest
from conftest import interaction, make_event, message, server

from mc_admin_qqbot.mcadmin import (
    McAdminResponseError,
    McAdminUnavailable,
    Monitoring,
    OnlinePlayer,
    Restoration,
    RestorationScope,
    ServerTask,
)
from mc_admin_qqbot.models import ServerGroupRule, User, utc_now
from mc_admin_qqbot.qq import Card, QQError


def sent(qq) -> Card:
    return qq.send_card.await_args.args[2]


def labels(qq) -> set[str]:
    return {button.label for row in sent(qq).rows for button in row}


def click(qq, label, *, group: str | None = "test-group", openid="user-one"):
    card_id = qq.send_card.await_args.kwargs["card_id"]
    row, col, data = next(
        (i, j, button.data)
        for i, row in enumerate(sent(qq).rows)
        for j, button in enumerate(row)
        if button.label == label
    )
    return interaction(
        data, button_id=f"{card_id}-{row}-{col}", group=group, openid=openid
    )


async def press(app, qq, label, **scope):
    await app.handle(click(qq, label, **scope))


async def set_admin(database, enabled=True):
    user = await database.observe(message(private=True))
    async with database.sessions.begin() as session:
        row = await session.get(User, user.id)
        assert row is not None
        row.is_superuser = enabled


def observation(state="HEALTHY", *, quality="ready", sampled_at=None, kind=None):
    return {
        "status": {
            "value": state,
            "quality": quality,
            "sampled_at": sampled_at or utc_now(),
        },
        "maintenance": {"active": kind is not None, "kind": kind},
    }


def monitor(mc, **servers):
    mc.monitoring.return_value = Monitoring.model_validate({"servers": servers})


def task(task_type="server_restart", status="running", **fields):
    return ServerTask.model_validate(
        {
            "task_id": "task-1",
            "task_type": task_type,
            "status": status,
            "created_at": utc_now(),
            **fields,
        }
    )


@pytest.fixture(autouse=True)
def service(mc):
    monitor(
        mc,
        vanilla=observation(),
        creative=observation("CREATED"),
        hidden=observation("EXISTS"),
    )
    mc.online_players.return_value = [
        OnlinePlayer(name="Steve", uuid="steve", player_db_id=1)
    ]
    mc.server_tasks.return_value = []
    mc.restoration.return_value = None
    mc.operate.return_value = "task-1"


async def test_only_admins_find_and_open_the_management_command(app, database, qq, mc):
    await app.handle(make_event("/help", role="member"))
    assert "/管理" not in (await database.pending())[-1].body
    await app.handle(make_event("/管理", role="member"))
    await app.handle(make_event("/管理", private=True))
    mc.servers.assert_not_awaited()

    await app.handle(make_event("/help", role="admin"))
    assert "/管理 [服务器]" in (await database.pending())[-1].body
    await app.handle(make_event("/管理", role="owner"))
    assert "**vanilla**" in sent(qq).markdown
    assert sent(qq).markdown.count("vanilla") == 1
    assert "在线玩家 ｜ 1 人" in sent(qq).markdown
    await press(app, qq, "启停与重启")
    assert "由机器人超级管理员执行" in sent(qq).markdown
    assert not labels(qq) & {"启动服务器", "停服", "重启"}
    mc.operate.assert_not_awaited()


async def test_switching_defaults_and_stopped_servers_uses_the_current_scope(
    app, database, qq, mc
):
    await database.save_default_server("hidden")
    await app.handle(make_event("/管理", role="admin"))
    assert "**vanilla**" in sent(qq).markdown
    await press(app, qq, "切换服务器")
    assert labels(qq) == {"vanilla", "creative", "返回"}
    await press(app, qq, "creative")
    assert "服务器状态 ｜ 已停止" in sent(qq).markdown
    assert "在线玩家 ｜ 0 人" in sent(qq).markdown
    await app.handle(make_event("/管理 /crea", role="owner"))
    assert "**creative**" in sent(qq).markdown
    for invalid in ("/管理 hidden", "/管理 /", "/管理 a b"):
        await app.handle(make_event(invalid, role="admin"))
    assert "找不到可管理的服务器" in (await database.pending())[-2].body
    assert "用法" in (await database.pending())[-1].body
    assert mc.online_players.await_count == 1

    await set_admin(database)
    await app.handle(make_event("/help", private=True))
    assert "/管理" in (await database.pending())[-1].body
    await app.handle(make_event("/管理", private=True))
    assert "**hidden**" in sent(qq).markdown


async def test_group_cards_bind_callback_and_sender_and_recheck_server_routing(
    app, database, qq, mc
):
    await app.handle(make_event("/管理", role="owner"))
    original = qq.send_card.await_args.kwargs["card_id"]
    await app.handle(make_event("/help", group="other-group", role="admin"))
    await app.handle(make_event("/help", openid="another", role="admin"))
    wrong_group = click(qq, "启停与重启", group="other-group")
    wrong_user = click(qq, "启停与重启", openid="another")
    changed_button = click(qq, "启停与重启")
    changed_button["d"]["data"]["resolved"]["button_data"] = (
        'manage:["submit","vanilla","stop"]'
    )
    for event, code in ((wrong_group, 3), (wrong_user, 4), (changed_button, 3)):
        await app.handle(event)
        qq.acknowledge.assert_awaited_with(event["d"]["id"], code=code)
    qq.send_card.assert_awaited_once()
    qq.recall.assert_not_awaited()

    async with database.sessions.begin() as session:
        session.add(
            ServerGroupRule(
                app_id=database.app_id, server_id="vanilla", group_openid="other-group"
            )
        )
    legit = click(qq, "启停与重启")
    await app.handle(legit)
    qq.acknowledge.assert_awaited_with(legit["d"]["id"])
    assert "找不到可管理的服务器" in sent(qq).markdown
    assert qq.send_card.await_args.kwargs["event_id"] == legit["id"]
    qq.recall.assert_awaited_once_with("group", "test-group", f"sent-{original}")
    assert [call[0] for call in qq.mock_calls[-2:]] == ["send_card", "recall"]
    await app.handle(legit)
    assert qq.send_card.await_count == 2
    mc.operate.assert_not_awaited()


async def test_read_permissions_follow_latest_observed_group_role(app, qq, mc):
    await app.handle(make_event("/管理", role="admin"))
    event = click(qq, "刷新状态")
    await app.handle(make_event("已变为群成员", role="member"))
    await app.handle(event)
    qq.acknowledge.assert_awaited_with(event["d"]["id"], code=4)
    qq.send_card.assert_awaited_once()
    mc.monitoring.assert_awaited_once()


async def test_stop_and_restart_require_confirmation_and_can_be_cancelled(
    app, database, qq, mc
):
    await set_admin(database)
    await app.handle(make_event("/管理", role="owner"))
    await press(app, qq, "启停与重启")
    await press(app, qq, "重启")
    assert "管理 › vanilla › 启停与重启 › 确认" in sent(qq).markdown
    assert "在线玩家 ｜ 1 人" in sent(qq).markdown
    assert "会中断当前游戏连接" in sent(qq).markdown
    await press(app, qq, "取消")
    await press(app, qq, "停服")
    mc.operate.assert_not_awaited()
    confirm = click(qq, "确认停服")
    await app.handle(confirm)
    assert "停服 ｜ 已受理" in sent(qq).markdown
    await app.handle(confirm)
    mc.operate.assert_awaited_once_with("vanilla", "stop")


async def test_submit_rechecks_superuser_and_restore_after_confirmation(
    app, database, qq, mc
):
    await set_admin(database)
    await app.handle(make_event("/管理", role="owner"))
    await press(app, qq, "启停与重启")
    await press(app, qq, "重启")
    await set_admin(database, False)
    await press(app, qq, "确认重启")
    mc.operate.assert_not_awaited()
    assert "由机器人超级管理员执行" in sent(qq).markdown

    await set_admin(database)
    await press(app, qq, "切换服务器")
    await press(app, qq, "vanilla")
    await press(app, qq, "启停与重启")
    await press(app, qq, "重启")
    mc.restoration.return_value = Restoration(status="running")
    await press(app, qq, "确认重启")
    mc.operate.assert_not_awaited()
    assert "正在维护或状态已变化" in sent(qq).markdown


async def test_restore_and_status_changes_hide_actions(app, database, qq, mc):
    await set_admin(database)
    await app.handle(make_event("/管理", private=True))
    await press(app, qq, "启停与重启", group=None)
    monitor(mc, vanilla=observation("STARTING", quality="stale", kind="backup"))
    await press(app, qq, "重启", group=None)
    assert "服务器状态已变化" in sent(qq).markdown
    await app.handle(make_event("/管理", private=True))
    assert "等待状态更新" in sent(qq).markdown
    assert "当前维护 ｜ 备份" in sent(qq).markdown
    mc.restoration.return_value = Restoration(status="pending")
    await press(app, qq, "启停与重启", group=None)
    assert "恢复任务已受理" in sent(qq).markdown
    assert labels(qq) == {"查看进度", "返回"}
    mc.operate.assert_not_awaited()


async def test_start_is_accepted_before_any_fresh_health_check(app, database, qq, mc):
    await set_admin(database)
    await app.handle(make_event("/管理 creative", private=True))
    await press(app, qq, "启停与重启", group=None)
    await press(app, qq, "启动服务器", group=None)
    assert "启动 ｜ 已受理" in sent(qq).markdown
    mc.operate.assert_awaited_once_with("creative", "start")
    mc.server_tasks.return_value = [task("server_start", "running", progress=32)]
    await press(app, qq, "刷新进度", group=None)
    assert "启动 ｜ 执行中 · 32%" in sent(qq).markdown

    finished = utc_now() - timedelta(seconds=2)
    mc.server_tasks.return_value = [
        task("server_start", "completed", ended_at=finished)
    ]
    monitor(mc, creative=observation(sampled_at=finished))
    await press(app, qq, "刷新进度", group=None)
    assert "启动 ｜ 等待游戏就绪" in sent(qq).markdown
    assert "已完成" not in sent(qq).markdown
    monitor(mc, creative=observation())
    await press(app, qq, "刷新进度", group=None)
    assert "启动 ｜ 已完成 · 游戏已就绪" in sent(qq).markdown


async def test_maintenance_shows_active_lifecycle_and_restore_then_latest_history(
    app, qq, mc
):
    await app.handle(make_event("/管理", role="admin"))
    await press(app, qq, "维护任务")
    assert "没有维护任务记录" in sent(qq).markdown
    mc.server_tasks.return_value = [
        task("server_restart", "failed", task_id="newer"),
        task("server_stop", "pending", created_at=utc_now() - timedelta(minutes=1)),
        task("snapshot_restore", "running", task_id="restore", progress=28),
    ]
    restoration = Restoration(
        status="running", operation_id="restore", scope=RestorationScope(kind="world")
    )
    mc.restoration.return_value = restoration
    await press(app, qq, "刷新进度")
    assert "停服 ｜ 已受理" in sent(qq).markdown
    assert "世界恢复 ｜ 执行中 · 28%" in sent(qq).markdown
    mc.server_tasks.return_value.pop(1)
    mc.restoration.side_effect = [
        None,
        Restoration(status="succeeded", is_rollback=True),
    ]
    await press(app, qq, "刷新进度")
    assert "重启 ｜ 失败，请在网页查看详情" in sent(qq).markdown
    assert "回滚恢复 ｜ 已完成" in sent(qq).markdown
    mc.restoration.side_effect = [
        None,
        McAdminUnavailable("snapshot repository offline"),
    ]
    await press(app, qq, "刷新进度")
    assert "历史恢复记录暂不可用" in sent(qq).markdown


@pytest.mark.parametrize(
    ("error", "notice"),
    [
        (McAdminResponseError(423), "冲突的操作"),
        (McAdminResponseError(403), "请求被服务器拒绝"),
        (McAdminResponseError(500), "请求结果待核对"),
        (McAdminUnavailable("timeout"), "请求结果待核对"),
    ],
)
async def test_failed_submission_reports_an_honest_state_without_resending(
    app, database, qq, mc, error, notice
):
    await set_admin(database)
    await app.handle(make_event("/管理 /creative", private=True))
    await press(app, qq, "启停与重启", group=None)
    mc.operate.side_effect = error
    start = click(qq, "启动服务器", group=None)
    await app.handle(start)
    assert notice in sent(qq).markdown
    assert "启动 ｜ 已受理" not in sent(qq).markdown
    await app.handle(start)
    mc.operate.assert_awaited_once_with("creative", "start")


async def test_lost_receipt_does_not_allow_replaying_one_click_start(
    app, database, qq, mc
):
    await set_admin(database)
    await app.handle(make_event("/管理 /creative", private=True))
    await press(app, qq, "启停与重启", group=None)
    start = click(qq, "启动服务器", group=None)
    retry = click(qq, "启动服务器", group=None)
    qq.recall.reset_mock()
    qq.send_card.side_effect = QQError(500)
    await app.handle(start)
    await app.handle(retry)
    qq.acknowledge.assert_awaited_with(retry["d"]["id"], code=3)
    qq.recall.assert_not_awaited()
    mc.operate.assert_awaited_once_with("creative", "start")


async def test_player_paging_and_image_buttons_reuse_roster_commands(
    app, database, qq, mc
):
    mc.online_players.return_value = [
        OnlinePlayer(name=f"Player{i:02d}", uuid=f"p-{i}", player_db_id=i)
        for i in range(21)
    ]
    mc.banned_players.return_value = ["Blocked"]
    await app.handle(make_event("/管理", role="admin"))
    await press(app, qq, "在线玩家")
    assert "在线玩家 1/2" in sent(qq).markdown
    assert "Player00" in sent(qq).markdown
    assert "Player20" not in sent(qq).markdown
    await press(app, qq, "下一页")
    assert "在线玩家 2/2" in sent(qq).markdown
    assert "Player20" in sent(qq).markdown
    ref = f"REFIDX_{qq.send_card.await_args.kwargs['card_id']}"
    button = next(
        button
        for row in sent(qq).rows
        for button in row
        if button.label == "封禁名单图片"
    )
    assert button.command
    qq.recall.reset_mock()
    await app.handle(make_event(button.data, ref=ref, role="admin"))
    mc.banned_players.assert_awaited_once_with("vanilla")
    assert (await database.pending())[-1].body.startswith(b"\x89PNG")
    qq.recall.assert_not_awaited()
    await press(app, qq, "返回")
    await press(app, qq, "刷新状态")
    assert "已刷新服务器状态" in sent(qq).markdown


async def test_unknown_or_unavailable_data_is_safe_to_reopen(app, database, qq, mc):
    await app.handle(make_event("/管理", role="admin", group="unrouted"))
    assert "当前会话没有可管理的服务器" in (await database.pending())[-1].body
    monitor(mc)
    await app.handle(make_event("/管理", role="admin"))
    assert "等待状态更新" in sent(qq).markdown
    mc.servers.side_effect = McAdminUnavailable("backend offline")
    await press(app, qq, "维护任务")
    assert "mc-admin 暂时不可用" in sent(qq).markdown
    mc.servers.side_effect = None
    mc.servers.return_value = []
    await press(app, qq, "切换服务器")
    assert "当前会话没有可管理的服务器" in sent(qq).markdown


async def test_switch_server_is_paginated_and_retains_scope(app, qq, mc):
    mc.servers.return_value += [server("extra1"), server("extra2"), server("extra3")]
    await app.handle(make_event("/管理", role="admin"))
    await press(app, qq, "切换服务器")
    assert "选择服务器 1/2" in sent(qq).markdown
    assert len(sent(qq).rows) == 5
    await press(app, qq, "下一页")
    assert "选择服务器 2/2" in sent(qq).markdown
    assert "hidden" not in labels(qq)
