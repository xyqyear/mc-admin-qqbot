from datetime import timedelta

import aiohttp
import pytest
from aiohttp import web
from conftest import chat, serve

from mc_admin_qqbot.mcadmin import (
    ChatEvent,
    HeartbeatFrame,
    McAdminClient,
    McAdminResponseError,
    McAdminUnavailable,
    Monitoring,
    StatusSample,
    StreamResetFrame,
    UnknownFrame,
)
from mc_admin_qqbot.models import utc_now


async def test_http_contract_server_selection_player_actions_and_task():
    calls = []
    sampled_at = utc_now().isoformat()

    async def handle(request):
        assert request.headers["Authorization"] == "Bearer master"
        path = request.path.removeprefix("/api")
        body = await request.json() if request.can_read_body else None
        calls.append((request.method, path, body))
        if path == "/servers/":
            return web.json_response(
                [
                    {"id": name, "name": name, "gamePort": port}
                    for name, port in [
                        ("vanilla", 25565),
                        ("starting", 25566),
                        ("hidden", 25567),
                    ]
                ]
            )
        if path == "/monitoring":
            return web.json_response(
                {
                    "servers": {
                        name: {
                            "status": {
                                "value": status,
                                "quality": "ready",
                                "sampled_at": sampled_at,
                            }
                        }
                        for name, status in [
                            ("vanilla", "HEALTHY"),
                            ("starting", "STARTING"),
                        ]
                    }
                }
            )
        if path.endswith("/online-players"):
            assert "vanilla" in path
            return web.json_response(
                [{"player_db_id": 1, "uuid": "uuid", "current_name": "Steve"}]
            )
        if path.startswith("/players/uuid/"):
            return web.json_response(
                {"player_db_id": 1, "uuid": "uuid", "current_name": "Steve"}
            )
        if path.endswith("/rcon"):
            return web.json_response({"output": "Added Steve"})
        if path.endswith("/operations"):
            return web.json_response({"task_id": "task-one"}, status=202)
        if path == "/tasks/task-one":
            return web.json_response({"status": "completed", "ended_at": sampled_at})
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/api/{tail:.*}", handle)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = McAdminClient(http, url + "/api/", "master")
        result = await client.overview({"hidden"})
        assert [server.status for server in result] == ["HEALTHY", "STARTING"]
        assert result[0].online_players[0].name == "Steve"
        assert (await client.player("uuid")).current_name == "Steve"
        assert await client.rcon("vanilla", "whitelist add Steve") == "Added Steve"
        await client.send_message("vanilla", "hello")
        await client.send_message("vanilla", "code", target_player="Steve")
        assert await client.restart("vanilla") == "task-one"
        assert (await client.task("task-one")).status == "completed"
        assert await client.status("vanilla") == "HEALTHY"
    assert (
        "POST",
        "/servers/vanilla/message",
        {"message": "code", "color": "yellow", "target_player": "Steve"},
    ) in calls
    assert ("POST", "/servers/vanilla/operations", {"action": "restart"}) in calls


@pytest.mark.parametrize(
    ("status", "payload", "is_json"),
    [
        (403, {"detail": "forbidden"}, True),
        (200, "malformed-json", False),
        (200, {"invalid": "schema"}, True),
    ],
)
async def test_mc_http_failures_are_safe_and_typed(status, payload, is_json):
    async def handle(_):
        if is_json:
            return web.json_response(payload, status=status)
        return web.Response(text=payload, status=status)

    app = web.Application()
    app.router.add_get("/api/monitoring", handle)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = McAdminClient(http, url + "/api", "master")
        with pytest.raises(McAdminResponseError, match=f"HTTP {status}"):
            await client.monitoring()


async def test_mc_transport_failure_and_ws_failure():
    app = web.Application()
    async with serve(app) as url:
        pass
    async with aiohttp.ClientSession() as http:
        client = McAdminClient(http, url + "/api", "master")
        with pytest.raises(McAdminUnavailable):
            await client.request("GET", "/servers/")
        with pytest.raises(McAdminUnavailable):
            await anext(client.events(None))


@pytest.mark.parametrize(
    "overrides",
    [
        {"quality": "stale"},
        {"value": None},
        {"sampled_at": None},
        {"sampled_at": utc_now().replace(tzinfo=None)},
        {"sampled_at": utc_now() - timedelta(seconds=30)},
    ],
)
def test_stale_unknown_or_incomplete_health_is_not_current(overrides):
    sample = StatusSample.model_validate(
        {
            "value": "HEALTHY",
            "quality": "ready",
            "sampled_at": utc_now(),
            **overrides,
        }
    )
    with pytest.raises(McAdminUnavailable):
        sample.current()


def test_restart_health_must_be_after_completion():
    now = utc_now()
    sample = StatusSample(value="HEALTHY", quality="ready", sampled_at=now)
    with pytest.raises(McAdminUnavailable):
        sample.current(after=now)
    assert sample.current(after=now - timedelta(seconds=1)) == "HEALTHY"
    with pytest.raises(McAdminUnavailable):
        Monitoring(servers={}).status("vanilla")


async def test_websocket_cursor_auth_and_wire_frames():
    received = []

    async def socket(request):
        received.append(
            (
                request.query.get("since"),
                request.headers.get("Authorization"),
                request.headers.get("Origin"),
            )
        )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(chat("、、hello", "13").model_dump_json())
        await ws.send_json({"type": "heartbeat", "timestamp": utc_now().isoformat()})
        await ws.send_json({"type": "future-event"})
        await ws.send_json({"type": "stream_reset", "reason": "cursor_too_old"})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/events", socket)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = McAdminClient(http, url + "/api", "master")
        frames = [frame async for frame in client.events("12")]
    assert [type(frame) for frame in frames] == [
        ChatEvent,
        HeartbeatFrame,
        UnknownFrame,
        StreamResetFrame,
    ]
    assert isinstance(frames[0], ChatEvent)
    assert frames[0].cursor == "13"
    assert received == [("12", "Bearer master", None)]


@pytest.mark.parametrize("frame", ["not-json", "null", "{}", '{"type":"chat"}'])
def test_bad_event_frames_are_rejected(frame):
    with pytest.raises(McAdminResponseError):
        McAdminClient.parse_frame(frame)


async def test_websocket_binary_frame_is_ignored():
    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(b"binary")
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/events", socket)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = McAdminClient(http, url + "/api", "master")
        assert [frame async for frame in client.events(None)] == []
