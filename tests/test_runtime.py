import asyncio
from base64 import b64decode
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from conftest import chat, make_event, serve
from PIL import Image
from sqlalchemy import select

from mc_admin_qqbot.__main__ import main, run
from mc_admin_qqbot.application import Application
from mc_admin_qqbot.mcadmin import McAdminClient
from mc_admin_qqbot.models import BindRequest, QQIdentity, User, utc_now
from mc_admin_qqbot.qq import QQClient


async def eventually(check):
    async with asyncio.timeout(4):
        while not await check():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_application_end_to_end_with_real_http_websockets_and_database(
    settings, database
):
    qq_events = asyncio.Queue()
    game_events = asyncio.Queue()
    qq_posts = []
    qq_uploads = []
    game_posts = []
    operations = []
    task_state = "running"
    health = "HEALTHY"
    ended = utc_now() - timedelta(seconds=1)

    async def token(_):
        return web.json_response({"access_token": "test-token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": f"ws://{request.host}/qq-ws"})

    async def socket(request, queue, official=False):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if official:
            await ws.send_json({"op": 10, "d": {"heartbeat_interval": 1000}})
            assert (await ws.receive_json())["op"] == 2
            await ws.send_json(
                {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session"}}
            )
        else:
            assert request.headers["Authorization"] == "Bearer test-master-token"

        async def forward():
            while True:
                await ws.send_json(await queue.get())

        sender = asyncio.create_task(forward())
        try:
            async for frame in ws:
                if (
                    official
                    and frame.type == aiohttp.WSMsgType.TEXT
                    and frame.json()["op"] == 1
                ):
                    await ws.send_json({"op": 11})
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
        return ws

    async def qq_socket(request):
        return await socket(request, qq_events, official=True)

    async def game_socket(request):
        return await socket(request, game_events)

    async def handle_qq(request):
        qq_posts.append(await request.json())
        return web.json_response(
            {
                "id": f"ROBOT1.0_{len(qq_posts)}",
                "ext_info": {"ref_idx": f"REFIDX_sent_{len(qq_posts)}"},
            }
        )

    async def upload_image(request):
        qq_uploads.append(await request.json())
        return web.json_response({"file_info": "whitelist-image"})

    async def handle_mc(request):
        path = request.path.removeprefix("/api")
        if request.method == "POST":
            body = await request.json()
            if path.endswith("/operations"):
                operations.append(body)
                return web.json_response({"task_id": "restart-task"}, status=202)
            if path == "/servers/vanilla/rcon":
                assert body == {"command": "whitelist list"}
                return web.json_response({"output": "There are 2 players: Steve, Alex"})
            game_posts.append(body)
            return web.Response(status=204)
        if path == "/servers/":
            return web.json_response(
                [{"id": "vanilla", "name": "原版", "gamePort": 25565}]
            )
        if path == "/monitoring":
            return web.json_response(
                {
                    "servers": {
                        "vanilla": {
                            "status": {
                                "value": health,
                                "quality": "ready",
                                "sampled_at": utc_now().isoformat(),
                            }
                        },
                    }
                }
            )
        if path.endswith("/online-players"):
            return web.json_response([])
        if path.startswith("/players/uuid"):
            return web.json_response(
                {
                    "player_db_id": 1,
                    "uuid": "player-uuid",
                    "current_name": "CurrentName",
                }
            )
        if path.startswith("/tasks/"):
            return web.json_response(
                {"status": task_state, "ended_at": ended.isoformat()}
            )
        raise AssertionError(path)

    server = web.Application()
    server.router.add_post("/token", token)
    server.router.add_get("/gateway/bot", gateway)
    server.router.add_get("/qq-ws", qq_socket)
    server.router.add_get("/api/events", game_socket)
    server.router.add_post("/v2/{kind}/{target}/messages", handle_qq)
    server.router.add_post("/v2/groups/test-group/files", upload_image)
    server.router.add_route("*", "/api/{tail:.*}", handle_mc)
    async with serve(server) as url, aiohttp.ClientSession() as http:
        settings.mc_admin_base_url = url + "/api"
        qq = QQClient(
            http, "test-app", "test-secret", api_url=url, token_url=url + "/token"
        )
        mc = McAdminClient(http, url + "/api", "test-master-token")
        app = Application(settings, database, qq, mc)
        running = asyncio.create_task(app.run())
        try:
            await asyncio.wait_for(qq.ready.wait(), 2)
            assert app.claim_token
            await qq_events.put(make_event(app.claim_token, private=True))

            async def is_admin():
                async with database.sessions() as session:
                    user = await session.scalar(select(User))
                    return user is not None and user.is_superuser

            await eventually(is_admin)
            await game_events.put(chat("、、bind").model_dump(mode="json"))

            async def has_private_code():
                return bool(game_posts)

            await eventually(has_private_code)
            assert game_posts[0]["target_player"] == "Steve"
            async with database.sessions() as session:
                code = (await session.scalar(select(BindRequest))).code
            assert code in game_posts[0]["message"]
            await qq_events.put(make_event(code, private=True))

            async def is_bound():
                async with database.sessions() as session:
                    return (
                        await session.scalar(select(User))
                    ).player_uuid == "player-uuid"

            await eventually(is_bound)
            await game_events.put(chat("、、来自游戏", "2").model_dump(mode="json"))

            async def has_relay():
                return any("来自游戏" in body["content"] for body in qq_posts)

            await eventually(has_relay)
            index = next(
                i for i, body in enumerate(qq_posts, 1) if "来自游戏" in body["content"]
            )
            relay = qq_posts[index - 1]
            assert "msg_id" not in relay
            await qq_events.put(make_event("引用回复", ref=f"REFIDX_sent_{index}"))

            async def has_reply():
                return any("CurrentName" in body["message"] for body in game_posts)

            await eventually(has_reply)
            assert game_posts[-1]["message"] == "*<CurrentName> 引用回复"
            await qq_events.put(make_event("/restart /vanilla", role="admin"))

            async def is_restarting():
                return bool(await database.watches()) and bool(operations)

            await eventually(is_restarting)
            assert not any("重启完成" in body["content"] for body in qq_posts)
            health = "STARTING"
            task_state = "completed"
            await asyncio.sleep(0.05)
            assert await database.watches()
            health = "HEALTHY"

            async def is_finished():
                return any("重启完成" in body["content"] for body in qq_posts)

            await eventually(is_finished)
            assert await database.watches() == []
            async with database.sessions() as session:
                identity = await session.get(QQIdentity, ("test-app", "user-one"))
                assert identity.profile["member_nickname"] == "测试群名片"

            list_request = make_event("/whitelist list /vanilla")
            await qq_events.put(list_request)

            async def has_picture_reply():
                return any(body.get("msg_type") == 7 for body in qq_posts)

            await eventually(has_picture_reply)
            (upload,) = qq_uploads
            assert upload["file_type"] == 1
            assert upload["srv_send_msg"] is False
            with Image.open(BytesIO(b64decode(upload["file_data"]))) as image:
                assert image.format == "PNG"
                image.verify()
            assert [body for body in qq_posts if body.get("msg_type") == 7] == [
                {
                    "msg_type": 7,
                    "media": {"file_info": "whitelist-image"},
                    "msg_id": list_request["d"]["id"],
                    "msg_seq": 1,
                }
            ]
        finally:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        assert not qq.ready.is_set()


async def test_process_closes_http_and_database_on_shutdown(settings):
    with patch(
        "mc_admin_qqbot.__main__.Application.run", side_effect=asyncio.CancelledError
    ):
        with pytest.raises(asyncio.CancelledError):
            await run(settings)


def test_cli_loads_environment_and_handles_keyboard_interrupt(settings):
    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    with (
        patch(
            "mc_admin_qqbot.__main__.Settings.model_validate", return_value=settings
        ) as load,
        patch("mc_admin_qqbot.__main__.asyncio.run", side_effect=interrupt),
    ):
        main()
    load.assert_called_once_with({})
