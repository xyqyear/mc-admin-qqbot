import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import nonebot
import pytest
from nonebot.drivers import Request, Response

import mc_admin_qqbot.mcadmin as mcadmin_module
from mc_admin_qqbot.mcadmin import (
    ChatEvent,
    HeartbeatFrame,
    McAdminClient,
    McAdminResponseError,
    McAdminUnavailable,
    StreamResetFrame,
    UnknownFrame,
)


def response(status_code: int, data=None) -> Response:
    content = None if data is None else json.dumps(data)
    return Response(status_code=status_code, content=content)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[McAdminClient, AsyncMock]:
    driver = nonebot.get_driver()
    request_mock = AsyncMock()
    monkeypatch.setattr(driver, "request", request_mock)
    return McAdminClient(driver, "http://mc-admin.test/api", "secret"), request_mock


async def test_overview_and_authorization_header(client) -> None:
    mcadmin, request_mock = client
    request_mock.side_effect = [
        response(
            200,
            [
                {
                    "id": "vanilla",
                    "name": "Vanilla",
                    "gamePort": 25565,
                }
            ],
        ),
        response(200, monitoring_payload()),
        response(200, [{"current_name": "Notch", "uuid": "uuid", "player_db_id": 7}]),
    ]
    servers = await mcadmin.overview()
    assert servers[0].game_port == 25565
    assert servers[0].status == "HEALTHY"
    assert servers[0].online_players[0].name == "Notch"
    requests = [call.args[0] for call in request_mock.await_args_list]
    assert [str(request.url) for request in requests] == [
        "http://mc-admin.test/api/servers/",
        "http://mc-admin.test/api/monitoring",
        "http://mc-admin.test/api/servers/vanilla/online-players",
    ]
    assert all(
        request.headers["Authorization"] == "Bearer secret" for request in requests
    )


def monitoring_payload(*, quality="ready", value="HEALTHY", sampled_at=None) -> dict:
    return {
        "servers": {
            "vanilla": {
                "status": {
                    "value": value,
                    "quality": quality,
                    "sampled_at": sampled_at or datetime.now(UTC).isoformat(),
                    "error": None,
                }
            }
        }
    }


@pytest.mark.parametrize("quality", ["pending", "stale", "unavailable"])
async def test_status_rejects_unusable_samples(client, quality) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = response(200, monitoring_payload(quality=quality))
    with pytest.raises(McAdminUnavailable):
        await mcadmin.status("vanilla")


@pytest.mark.parametrize("offset", [-10, -1, 0])
async def test_restart_status_requires_sample_after_request(client, offset) -> None:
    mcadmin, request_mock = client
    requested_at = datetime.now(UTC)
    request_mock.return_value = response(
        200,
        monitoring_payload(
            sampled_at=(requested_at + timedelta(seconds=offset)).isoformat()
        ),
    )
    with pytest.raises(McAdminUnavailable):
        await mcadmin.status("vanilla", after=requested_at)


async def test_status_accepts_fresh_post_request_sample(client) -> None:
    mcadmin, request_mock = client
    requested_at = datetime.now(UTC) - timedelta(seconds=1)
    request_mock.return_value = response(200, monitoring_payload())
    assert await mcadmin.status("vanilla", after=requested_at) == "HEALTHY"


@pytest.mark.parametrize(
    "sample",
    [
        {"value": None, "quality": "ready", "sampled_at": None},
        {"value": "HEALTHY", "quality": "ready", "sampled_at": "2026-01-01T00:00:00"},
        {"value": "HEALTHY", "quality": "ready", "sampled_at": "2026-01-01T00:00:00Z"},
    ],
)
async def test_status_rejects_missing_or_expired_timestamp(client, sample) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = response(
        200, {"servers": {"vanilla": {"status": sample}}}
    )
    with pytest.raises(McAdminUnavailable):
        await mcadmin.status("vanilla")


async def test_missing_server_observation_is_unavailable(client) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = response(200, {"servers": {}})
    with pytest.raises(McAdminUnavailable):
        await mcadmin.status("vanilla")


@pytest.mark.parametrize("status", ["EXISTS", "CREATED", "REMOVED"])
async def test_overview_does_not_fetch_players_for_stopped_servers(client, status):
    mcadmin, request_mock = client
    request_mock.side_effect = [
        response(200, [{"id": "vanilla", "name": "Vanilla", "gamePort": 25565}]),
        response(200, monitoring_payload(value=status)),
    ]
    servers = await mcadmin.overview()
    assert servers[0].status == status
    assert servers[0].online_players == []
    assert request_mock.await_count == 2


async def test_overview_does_not_report_unknown_status_as_offline(client):
    mcadmin, request_mock = client
    request_mock.side_effect = [
        response(200, [{"id": "vanilla", "name": "Vanilla", "gamePort": 25565}]),
        response(200, monitoring_payload(quality="stale")),
    ]
    with pytest.raises(McAdminUnavailable):
        await mcadmin.overview()


async def test_all_http_operations(client) -> None:
    mcadmin, request_mock = client
    request_mock.side_effect = [
        response(200, {"player_db_id": 1, "uuid": "abc", "current_name": "Alice"}),
        response(200, {"output": "ok"}),
        response(204),
        response(200, {"message": "issued"}),
        response(200, monitoring_payload()),
    ]
    assert (await mcadmin.player("abc")).current_name == "Alice"
    assert await mcadmin.rcon("vanilla", "list") == "ok"
    await mcadmin.send_message(
        "vanilla", 'quote " \\ \n 你好', target_player="Alice", color="aqua"
    )
    await mcadmin.restart("vanilla")
    assert await mcadmin.status("vanilla") == "HEALTHY"
    message_request: Request = request_mock.await_args_list[2].args[0]
    assert isinstance(message_request.content, str)
    payload = json.loads(message_request.content)
    assert payload == {
        "message": 'quote " \\ \n 你好',
        "target_player": "Alice",
        "color": "aqua",
    }


async def test_http_errors_and_invalid_json(client) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = response(409, {"detail": "not healthy"})
    with pytest.raises(McAdminResponseError, match="not healthy") as caught:
        await mcadmin.rcon("vanilla", "list")
    assert caught.value.status_code == 409
    request_mock.return_value = Response(status_code=200, content="not json")
    with pytest.raises(McAdminResponseError, match="invalid JSON"):
        await mcadmin.overview()
    request_mock.side_effect = OSError("offline")
    with pytest.raises(McAdminUnavailable):
        await mcadmin.overview()


async def test_http_error_fallbacks_and_invalid_utf8(client) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = Response(status_code=503, content="not json")
    with pytest.raises(McAdminResponseError, match="HTTP 503"):
        await mcadmin.overview()
    request_mock.return_value = Response(status_code=500)
    with pytest.raises(McAdminResponseError, match="HTTP 500"):
        await mcadmin.overview()
    request_mock.return_value = Response(status_code=200, content=b"\xff")
    with pytest.raises(McAdminResponseError, match="invalid UTF-8"):
        await mcadmin.overview()


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("overview", {"not": "a list"}),
        ("player", {"uuid": "missing fields"}),
        ("rcon", {"missing": "output"}),
        ("status", {"missing": "status"}),
    ],
)
async def test_invalid_response_models_are_mcadmin_errors(
    client, operation: str, payload: object
) -> None:
    mcadmin, request_mock = client
    request_mock.return_value = response(200, payload)
    call = getattr(mcadmin, operation)
    arguments = {
        "overview": (),
        "player": ("uuid",),
        "rcon": ("vanilla", "list"),
        "status": ("vanilla",),
    }[operation]
    with pytest.raises(McAdminResponseError, match="invalid response"):
        await call(*arguments)


def test_driver_capabilities_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="HTTP clients"):
        McAdminClient(object(), "http://mc-admin.test/api", "secret")  # type: ignore[arg-type]

    class NeverMatches:
        pass

    monkeypatch.setattr(mcadmin_module, "HTTPClientMixin", object)
    monkeypatch.setattr(mcadmin_module, "WebSocketClientMixin", NeverMatches)
    with pytest.raises(RuntimeError, match="WebSocket clients"):
        McAdminClient(object(), "http://mc-admin.test/api", "secret")  # type: ignore[arg-type]


def test_parse_event_frames() -> None:
    timestamp = datetime.now(UTC).isoformat()
    chat = McAdminClient.parse_frame(
        json.dumps(
            {
                "type": "chat",
                "cursor": "1",
                "server_id": "vanilla",
                "timestamp": timestamp,
                "player": {"name": "Alice", "uuid": "abc", "player_db_id": 1},
                "message": "\\\\hello",
            }
        )
    )
    assert isinstance(chat, ChatEvent)
    assert chat.cursor == "1"
    heartbeat = McAdminClient.parse_frame(
        json.dumps({"type": "heartbeat", "timestamp": timestamp})
    )
    assert isinstance(heartbeat, HeartbeatFrame)
    reset = McAdminClient.parse_frame(
        json.dumps({"type": "stream_reset", "reason": "cursor_too_old"})
    )
    assert isinstance(reset, StreamResetFrame)
    unknown = McAdminClient.parse_frame(json.dumps({"type": "player_join"}))
    assert isinstance(unknown, UnknownFrame)
    assert isinstance(
        McAdminClient.parse_frame(json.dumps({"type": "player_join"}).encode()),
        UnknownFrame,
    )
    with pytest.raises(McAdminResponseError, match="invalid event"):
        McAdminClient.parse_frame("[]")


async def test_event_stream_builds_since_query(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcadmin, _ = client
    driver = nonebot.get_driver()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.frames = [
                json.dumps(
                    {
                        "type": "stream_reset",
                        "reason": "cursor_too_old",
                    }
                )
            ]

        async def receive(self):
            return self.frames.pop(0)

    requests: list[Request] = []

    @asynccontextmanager
    async def websocket(request: Request):
        requests.append(request)
        yield FakeWebSocket()

    monkeypatch.setattr(driver, "websocket", websocket)
    iterator = mcadmin.events("cursor with spaces")
    frame = await anext(iterator)
    await iterator.aclose()
    assert isinstance(frame, StreamResetFrame)
    assert str(requests[0].url) == (
        "ws://mc-admin.test/api/events?since=cursor+with+spaces"
    )
    assert requests[0].headers["Authorization"] == "Bearer secret"


async def test_event_stream_without_cursor_and_disconnects(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcadmin, _ = client
    driver = nonebot.get_driver()
    requests: list[Request] = []

    @asynccontextmanager
    async def websocket(request: Request):
        requests.append(request)
        raise OSError("disconnected")
        yield  # pragma: no cover

    monkeypatch.setattr(driver, "websocket", websocket)
    with pytest.raises(McAdminUnavailable, match="disconnected"):
        await anext(mcadmin.events(None))
    assert str(requests[0].url) == "ws://mc-admin.test/api/events"


async def test_event_stream_preserves_frame_validation_errors(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcadmin, _ = client
    driver = nonebot.get_driver()

    class FakeWebSocket:
        async def receive(self):
            return "[]"

    @asynccontextmanager
    async def websocket(_request: Request):
        yield FakeWebSocket()

    monkeypatch.setattr(driver, "websocket", websocket)
    with pytest.raises(McAdminResponseError, match="invalid event"):
        await anext(mcadmin.events(None))
