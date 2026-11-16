import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from mc_admin_qqbot.application import Application
from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.database import Database
from mc_admin_qqbot.mcadmin import (
    ChatEvent,
    McAdminClient,
    PlayerDetail,
    ServerOverview,
)
from mc_admin_qqbot.models import utc_now
from mc_admin_qqbot.qq import QQClient, QQMessage, SentMessage

ids = itertools.count(1)


def make_event(
    text: str = "普通聊天",
    *,
    private: bool = False,
    openid: str = "user-one",
    group: str = "test-group",
    role: str | None = "member",
    ref: str | None = None,
    **author: Any,
) -> dict[str, Any]:
    number = next(ids)
    profile = {
        "id": openid,
        "user_openid" if private else "member_openid": openid,
        "username": "测试昵称",
        "avatar": "https://example.test/avatar",
        "bot": False,
        **author,
    }
    if not private and role is not None:
        profile["member_role"] = role
        profile.setdefault("member_nickname", "测试群名片")
    ext = [f"msg_idx=REFIDX_{number}", "auth_token=sensitive-scene-token"]
    if ref:
        ext.append(f"ref_msg_idx={ref}")
    data = {
        "id": f"message-{number}",
        "author": profile,
        "content": text,
        "message_type": 103 if ref else 0,
        "message_scene": {"ext": ext},
    }
    if not private:
        data["group_openid"] = group
    return {
        "op": 0,
        "s": number,
        "t": "C2C_MESSAGE_CREATE" if private else "GROUP_MESSAGE_CREATE",
        "d": data,
    }


def message(text: str = "", **kwargs: Any) -> QQMessage:
    return QQMessage.from_event(make_event(text, **kwargs))


def chat(text: str, cursor: str = "1", **overrides: Any) -> ChatEvent:
    return ChatEvent.model_validate(
        {
            "type": "chat",
            "cursor": cursor,
            "server_id": "vanilla",
            "timestamp": utc_now(),
            "player": {"name": "Steve", "uuid": "player-uuid", "player_db_id": 1},
            "message": text,
            **overrides,
        }
    )


def server(server_id: str = "vanilla", port: int = 25565) -> ServerOverview:
    return ServerOverview(
        id=server_id,
        name=server_id,
        gamePort=port,
        status="HEALTHY",
        online_players=[],
    )


@asynccontextmanager
async def serve(app: web.Application):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "qq_app_id": "test-app",
        "qq_app_secret": "test-secret",
        "qq_group_openid": "test-group",
        "mc_admin_base_url": "http://example.test/api",
        "mc_admin_master_token": "test-master-token",
        "excluded_servers": {"hidden"},
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}",
        "restart_poll_interval_seconds": 0.01,
        "_env_file": None,
    }
    return Settings(**values)


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings.database_url, settings.qq_app_id)
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def mc() -> AsyncMock:
    client = AsyncMock(spec=McAdminClient)
    client.overview.return_value = [server(), server("creative", 25566)]
    client.player.return_value = PlayerDetail(
        player_db_id=1, uuid="player-uuid", current_name="NewName"
    )
    client.rcon.return_value = "Added Steve"
    client.restart.return_value = "restart-task"
    return client


@pytest.fixture
def qq() -> AsyncMock:
    client = AsyncMock(spec=QQClient)
    client.send.return_value = SentMessage("sent-id", "REFIDX_sent")
    return client


@pytest.fixture
def app(settings: Settings, database: Database, mc: AsyncMock, qq: AsyncMock):
    return Application(settings, database, qq, mc)
