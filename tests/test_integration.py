import asyncio
import socket
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import nonebot
import pytest
import pytest_asyncio
import uvicorn
from alembic.migration import MigrationContext
from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Reply, Sender
from nonebug import App

from mc_admin_qqbot.application import Application
from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.mcadmin import ChatEvent
from mc_admin_qqbot.models import ContextType
from mc_admin_qqbot.runtime import get_application, set_application


def group_event(message: str, *, message_id: int) -> GroupMessageEvent:
    parsed = Message(message)
    return GroupMessageEvent(
        time=int(time.time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=12345,
        message_type="group",
        message_id=message_id,
        message=parsed,
        original_message=parsed,
        raw_message=str(parsed),
        font=0,
        sender=Sender(user_id=12345, nickname="Tester", role="member"),
        to_me=False,
        group_id=20000,
    )


@pytest_asyncio.fixture(loop_scope="function")
async def fake_mcadmin() -> AsyncIterator[tuple[str, dict[str, Any]]]:
    app = FastAPI()
    state: dict[str, Any] = {
        "messages": [],
        "operations": [],
        "websocket_since": [],
    }

    async def authorize(
        authorization: str | None = Header(default=None),
    ) -> None:
        if authorization != "Bearer mc-admin-test-token":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/api/servers/", dependencies=[Depends(authorize)])
    async def overview() -> list[dict[str, Any]]:
        return [
            {
                "id": "vanilla",
                "name": "Vanilla",
                "gamePort": 25565,
            }
        ]

    @app.get("/api/players/uuid/{player_uuid}", dependencies=[Depends(authorize)])
    async def player(player_uuid: str) -> dict[str, Any]:
        return {
            "player_db_id": 1,
            "uuid": player_uuid,
            "current_name": "AliceRenamed",
        }

    @app.post("/api/servers/{server_id}/message", dependencies=[Depends(authorize)])
    async def message(
        server_id: str, payload: dict[str, Any] = Body()
    ) -> dict[str, bool]:
        state["messages"].append((server_id, payload))
        return {"ok": True}

    @app.post("/api/servers/{server_id}/rcon", dependencies=[Depends(authorize)])
    async def rcon(server_id: str, payload: dict[str, Any] = Body()) -> dict[str, str]:
        state["rcon"] = (server_id, payload)
        return {"output": "There are no whitelisted players"}

    @app.post("/api/servers/{server_id}/operations", dependencies=[Depends(authorize)])
    async def operation(
        server_id: str, payload: dict[str, Any] = Body()
    ) -> dict[str, str]:
        state["operations"].append((server_id, payload))
        return {"message": "issued"}

    @app.get("/api/monitoring", dependencies=[Depends(authorize)])
    async def monitoring() -> dict:
        return {
            "servers": {
                "vanilla": {
                    "status": {
                        "value": "HEALTHY",
                        "quality": "ready",
                        "sampled_at": datetime.now(UTC).isoformat(),
                        "error": None,
                    }
                }
            }
        }

    @app.get(
        "/api/servers/{server_id}/online-players", dependencies=[Depends(authorize)]
    )
    async def online_players(server_id: str) -> list[dict]:
        return [{"current_name": "Alice", "uuid": "uuid-a", "player_db_id": 1}]

    @app.websocket("/api/events")
    async def events(websocket: WebSocket, since: str | None = None) -> None:
        if websocket.headers.get("authorization") != ("Bearer mc-admin-test-token"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        state["websocket_since"].append(since)
        await websocket.send_json(
            {
                "type": "chat",
                "cursor": "event-1",
                "server_id": "vanilla",
                "timestamp": datetime.now(UTC).isoformat(),
                "player": {
                    "name": "Alice",
                    "uuid": "uuid-a",
                    "player_db_id": 1,
                },
                "message": "\\\\hello from game",
            }
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port = server_socket.getsockname()[1]
    server_socket.close()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    if not server.started:
        server.should_exit = True
        await task
        raise RuntimeError("Fake mc-admin server did not start")
    try:
        yield f"http://127.0.0.1:{port}/api", state
    finally:
        server.should_exit = True
        await task


async def wait_for_route(application: Application, message_id: str) -> str:
    for _ in range(200):
        route = await application.repository.route_for(
            bot_self_id="10000",
            context_type=ContextType.GROUP,
            context_id="20000",
            message_id=message_id,
        )
        if route is not None:
            return route
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for outbound delivery")


async def test_application_event_delivery_and_nonebug_command(
    app: App,
    tmp_path: Path,
    fake_mcadmin: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mc_admin_qqbot import plugin

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    base_url, state = fake_mcadmin
    settings = Settings.model_validate(
        {
            "qq_bot_id": 10000,
            "qq_group_id": 20000,
            "onebot_access_token": "onebot-test-token",
            "mc_admin_base_url": base_url,
            "mc_admin_master_token": "mc-admin-test-token",
            "superusers": {30000},
            "excluded_servers": {"hidden"},
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'integration.db').as_posix()}"
            ),
            "restart_poll_interval_seconds": 0.01,
        }
    )
    original_application = get_application()
    driver = nonebot.get_driver()
    application = Application(settings, driver)
    outbound_bot = AsyncMock()
    outbound_bot.call_api.return_value = {"message_id": 700}
    monkeypatch.setattr("nonebot.get_bot", lambda _self_id: outbound_bot)
    set_application(application)
    await driver._lifespan.startup()
    try:
        assert (await application.client.overview())[0].id == "vanilla"
        assert await application.client.status("vanilla") == "HEALTHY"
        listing = await application.commands.list_servers.execute()
        assert listing.text == "[vanilla]：Alice"
        event_stream = application.client.events(None)
        streamed_event = await asyncio.wait_for(anext(event_stream), timeout=5)
        await event_stream.aclose()
        assert isinstance(streamed_event, ChatEvent)
        await application.startup()
        assert await wait_for_route(application, "700") == "vanilla"
        assert state["websocket_since"] == [None, None]
        relay = outbound_bot.call_api.await_args.kwargs["message"]
        assert relay[0].type == "text"
        assert relay[0].data["text"] == "[vanilla] <Alice>：hello from game"

        async with application.database.engine.connect() as connection:
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
        assert revision == "20260729_initial_schema"
        assert await application.repository.bind("12345", "uuid-a")

        event = group_event("/say hello /van", message_id=10)
        async with app.test_matcher(plugin.say_matcher) as context:
            adapter = nonebot.get_adapter(Adapter)
            bot = context.create_bot(base=Bot, adapter=adapter, self_id="10000")
            context.receive_event(bot, event)
        assert state["messages"][-1] == (
            "vanilla",
            {
                "message": "*<AliceRenamed> hello",
                "target_player": None,
                "color": "yellow",
            },
        )

        reply_event = group_event("answer", message_id=11)
        reply_event.reply = Reply(
            time=int(time.time()),
            message_type="group",
            message_id=700,
            real_id=700,
            sender=Sender(user_id=10000),
            message=Message("relay"),
        )
        async with app.test_matcher(plugin.reply_matcher) as context:
            adapter = nonebot.get_adapter(Adapter)
            bot = context.create_bot(base=Bot, adapter=adapter, self_id="10000")
            context.receive_event(bot, reply_event)
        assert state["messages"][-1][1]["message"] == "*<AliceRenamed> answer"
    finally:
        await application.shutdown()
        await driver._lifespan.shutdown()
        set_application(original_application)
