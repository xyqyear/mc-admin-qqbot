import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from conftest import chat, make_event, message
from sqlalchemy import select

from mc_admin_qqbot.domain import UserInputError
from mc_admin_qqbot.models import BindRequest, GroupMember, QQIdentity, User, utc_now


async def test_profiles_follow_openid_across_group_and_private(app, database):
    await app.handle(make_event(username="旧昵称", member_nickname="旧群名片"))
    async with database.sessions.begin() as session:
        user = await session.scalar(select(User))
        user.qq_id = "12345678"
        user_id = user.id
    await app.handle(
        make_event(username="新昵称", member_nickname="新群名片", role="admin")
    )
    await app.handle(make_event(private=True, username="私聊昵称", avatar=""))
    async with database.sessions() as session:
        users = list(await session.scalars(select(User)))
        assert len(users) == 1
        assert users[0].id == user_id
        assert users[0].qq_id == "12345678"
        identity = await session.get(QQIdentity, ("test-app", "user-one"))
        assert identity.username == "私聊昵称"
        assert identity.avatar_url == "https://example.test/avatar"
        assert identity.profile["member_nickname"] == "新群名片"
        assert "auth_token" not in str(identity.profile)
        member = await session.get(GroupMember, ("test-app", "test-group", "user-one"))
        assert (member.member_nickname, member.member_role) == ("新群名片", "admin")
        assert identity.last_seen_at >= identity.first_seen_at
    sparse = make_event()
    sparse["d"]["author"] = {"member_openid": "user-one"}
    await app.handle(sparse)
    async with database.sessions() as session:
        identity = await session.get(QQIdentity, ("test-app", "user-one"))
        assert identity.username == "私聊昵称"
    assert await database.pending() == []


async def test_game_binding_requires_private_code_and_retains_identity(app, database):
    await app.relay.process(chat(r"\\bind"))
    pending = await database.pending()
    assert len(pending) == 1
    assert pending[0].channel == "game"
    assert pending[0].destination == "Steve"
    async with database.sessions() as session:
        request = await session.scalar(select(BindRequest))
        code = request.code
        assert len(code) == 4
        assert code.isdecimal()
    await app.handle(make_event(code))
    async with database.sessions() as session:
        assert (await session.scalar(select(User))).player_uuid is None
    await app.handle(make_event(code, private=True))
    async with database.sessions.begin() as session:
        user = await session.scalar(select(User))
        assert user.player_uuid == "player-uuid"
        assert user.qq_id is None
        user.is_superuser = True
    await app.handle(make_event(code, private=True))
    assert "无效" in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind get", "2"))
    assert "测试昵称" in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind", "3"))
    assert "已经绑定" in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind remove", "4"))
    async with database.sessions() as session:
        user = await session.scalar(select(User))
        assert user.player_uuid is None
        assert user.is_superuser
        assert await session.get(QQIdentity, ("test-app", "user-one")) is not None


async def test_binding_expiration_reissue_cancel_and_arguments(app, database):
    for cursor, text, expected in [
        ("1", r"\\bind get", "没有绑定"),
        ("2", r"\\bind remove", "没有绑定"),
        ("3", r"\\bind 12345678", "绑定命令"),
        ("4", r"\\bind help", "绑定命令"),
    ]:
        await app.relay.process(chat(text, cursor))
        assert expected in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind", "5"))
    async with database.sessions.begin() as session:
        old = await session.scalar(select(BindRequest))
        old_code = old.code
        old.expires_at = utc_now() - timedelta(seconds=1)
    await app.handle(make_event(old_code, private=True))
    assert "过期" in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind", "6"))
    await app.relay.process(chat("、、bind", "7"))
    async with database.sessions() as session:
        assert len(list(await session.scalars(select(BindRequest)))) == 1
    await app.relay.process(chat("、、bind remove", "8"))
    assert "已取消" in (await database.pending())[-1].body
    await app.relay.process(chat("、、bind", "9", player={"name": "Unknown"}))
    assert "玩家身份" in (await database.pending())[-1].body


async def test_codes_are_one_use_and_conflicts_roll_back(app, database):
    first = await database.observe(message(private=True))
    second = await database.observe(message(private=True, openid="user-two"))
    await app.relay.process(chat("、、bind"))
    async with database.sessions() as session:
        code = (await session.scalar(select(BindRequest))).code
    results = await asyncio.gather(
        app.bindings.confirm(first.id, code), app.bindings.confirm(second.id, code)
    )
    assert sum("绑定成功" in result for result in results) == 1
    async with database.sessions.begin() as session:
        users = list(await session.scalars(select(User).order_by(User.id)))
        bound = next(user for user in users if user.player_uuid)
        free = next(user for user in users if not user.player_uuid)
        session.add(
            BindRequest(
                code="5678",
                player_uuid="another-player",
                player_name="Alex",
                server_id="vanilla",
                expires_at=utc_now() + timedelta(minutes=1),
            )
        )
        session.add(
            BindRequest(
                code="1234",
                player_uuid=bound.player_uuid,
                player_name="Steve",
                server_id="vanilla",
                expires_at=utc_now() + timedelta(minutes=1),
            )
        )
    with pytest.raises(UserInputError, match="你已经绑定"):
        await app.bindings.confirm(bound.id, "5678")
    with pytest.raises(UserInputError, match="其他机器人"):
        await app.bindings.confirm(free.id, "1234")
    async with database.sessions() as session:
        assert await session.get(BindRequest, "5678")
        assert await session.get(BindRequest, "1234")


async def test_code_collision_get_fallback_and_rate_limit(app, database):
    await database.observe(message())
    async with database.sessions.begin() as session:
        session.add(
            BindRequest(
                code="1000",
                player_uuid="other-player",
                player_name="Alex",
                server_id="vanilla",
                expires_at=utc_now() + timedelta(minutes=1),
            )
        )
    with patch("mc_admin_qqbot.binding.secrets.randbelow", side_effect=[0, 1]):
        await app.relay.process(chat("、、bind"))
    async with database.sessions() as session:
        assert await session.get(BindRequest, "1001")
    for _ in range(6):
        await app.handle(make_event("9999", private=True))
    assert "频繁" in (await database.pending())[-1].body
    with patch("mc_admin_qqbot.application.time.monotonic", return_value=10**12):
        assert app.allow_code_attempt(1)
    async with database.sessions.begin() as session:
        user = await session.scalar(select(User))
        user.player_uuid = "player-uuid"
        identity = await session.get(QQIdentity, ("test-app", "user-one"))
        identity.username = None
    await app.relay.process(chat("、、bind get", "2"))
    assert "QQ用户" in (await database.pending())[-1].body


async def test_superuser_claim_is_private_once_per_start_and_persisted(
    app, database, capsys
):
    await app.startup()
    token = app.claim_token
    assert token
    assert len(token) >= 32
    assert token in capsys.readouterr().out
    await app.handle(make_event(token))
    async with database.sessions() as session:
        assert not (await session.scalar(select(User))).is_superuser
    await app.handle(make_event(token, private=True))
    assert "超级管理员" in (await database.pending())[-1].body
    await app.handle(make_event(token, private=True, openid="user-two"))
    async with database.sessions() as session:
        users = list(await session.scalars(select(User).order_by(User.id)))
        assert [u.is_superuser for u in users] == [True, False]
    await app.startup()
    assert app.claim_token
    assert app.claim_token != token
    await app.handle(make_event(app.claim_token, private=True, openid="user-two"))
    async with database.sessions() as session:
        assert all(u.is_superuser for u in await session.scalars(select(User)))


async def test_ignored_sources_duplicates_and_private_commands(app, database, mc):
    event = make_event("/list")
    await app.handle(make_event("/list", bot=True))
    await app.handle(make_event("/list", private=True))
    assert not mc.overview.called
    await app.handle(event)
    await app.handle(event)
    assert mc.overview.call_count == 1
    assert len(await database.pending()) == 1
    app._seen = {f"old-{i}": None for i in range(4096)}
    await app.handle(make_event("普通聊天"))
    assert len(app._seen) == 4096
    assert "old-0" not in app._seen
