import asyncio

import aiohttp
import pytest
from aiohttp import web
from conftest import make_event, message, serve

from mc_admin_qqbot.cards import Cards
from mc_admin_qqbot.qq import Button, Card, QQClient, QQError, QQMessage


def test_actual_group_quote_shape_and_private_identity():
    event = make_event("<@bot-openid>  引用回复", ref="REFIDX_original")
    event["d"]["mentions"] = [{"id": "bot-openid", "is_you": True, "bot": True}]
    event["d"]["msg_elements"] = [{"message_type": 103}]
    event["d"]["parallel_message"] = {"msg_nodes": [{"text": "被引用消息"}]}
    parsed = QQMessage.from_event(event)
    assert parsed.content == "引用回复"
    assert parsed.ref_msg_idx == "REFIDX_original"
    assert "auth_token" not in repr(parsed)
    private = QQMessage.from_event(make_event(private=True))
    assert private.openid == parsed.openid
    assert private.scope == "private"
    assert private.destination == parsed.openid
    event["d"]["mentions"] = [{"id": "someone-else", "is_you": False}]
    assert QQMessage.from_event(event).content.startswith("<@bot-openid>")
    event["d"].pop("content")
    event["d"].pop("message_scene")
    assert QQMessage.from_event(event).content == ""


async def test_official_http_active_passive_replies_and_token_refresh():
    tokens = []
    sent = []
    first = True

    async def token(request):
        assert await request.json() == {"appId": "app", "clientSecret": "secret"}
        tokens.append("token-" + str(len(tokens)))
        return web.json_response({"access_token": tokens[-1], "expires_in": "7200"})

    async def send(request):
        nonlocal first
        assert request.headers["X-Union-Appid"] == "app"
        if first:
            first = False
            return web.json_response({"code": 11244}, status=401)
        assert request.headers["Authorization"] == "QQBot token-1"
        sent.append((request.path, await request.json()))
        return web.json_response(
            {"id": "ROBOT1.0_response", "ext_info": {"ref_idx": "REFIDX_response"}}
        )

    async def state(_):
        return web.json_response(
            {"allow_proactive_msg": True, "recv_msg_setting": "all"}
        )

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_post("/v2/{kind}/{target}/messages", send)
    app.router.add_get("/v2/groups/group/bot_state", state)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        result = await client.send("group", "group", "主动")
        assert result.id == "ROBOT1.0_response"
        assert result.ref_idx == "REFIDX_response"
        await client.send("group", "group", "回复", msg_id="incoming")
        await client.send(
            "private", "user", "私聊回复", msg_id="private-incoming", msg_seq=2
        )
        assert (await client.group_state("group"))["recv_msg_setting"] == "all"
    assert len(tokens) == 2
    assert sent == [
        ("/v2/groups/group/messages", {"msg_type": 0, "content": "主动"}),
        (
            "/v2/groups/group/messages",
            {"msg_type": 0, "content": "回复", "msg_id": "incoming", "msg_seq": 1},
        ),
        (
            "/v2/users/user/messages",
            {
                "msg_type": 0,
                "content": "私聊回复",
                "msg_id": "private-incoming",
                "msg_seq": 2,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("scope", "destination", "kind", "status"),
    [("private", "user-one", "users", 204), ("group", "test-group", "groups", 200)],
)
async def test_button_cards_are_recalled_in_their_original_conversation(
    scope, destination, kind, status
):
    posts, deletions, acknowledgements = [], [], []

    async def token(_):
        return web.json_response({"access_token": "test-token", "expires_in": 7200})

    async def post(request):
        posts.append((request.path, await request.json()))
        return web.json_response(
            {
                "id": f"ROBOT1.0_{len(posts)}",
                "ext_info": {"ref_idx": f"REFIDX_{len(posts)}"},
            }
        )

    async def recall(request):
        deletions.append((request.path, request.query["hidetip"], len(posts)))
        return web.Response(status=204) if status == 204 else web.json_response({})

    async def acknowledge(request):
        acknowledgements.append((request.path, await request.json()))
        return web.Response(status=200, text="OK" if scope == "group" else "")

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_post("/v2/{kind}/{target}/messages", post)
    app.router.add_put("/interactions/{id}", acknowledge)
    app.router.add_delete("/v2/{kind}/{target}/messages/{id}", recall)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        cards = Cards(client)
        card = Card(
            "设置", [[Button("继续", "next"), Button("自定义", "/test ", command=True)]]
        )
        await cards.send(scope, destination, card, msg_id="incoming")
        path, payload = posts[0]
        assert path == f"/v2/{kind}/{destination}/messages"
        assert payload["msg_type"] == 2
        assert payload["msg_id"] == "incoming"
        callback, custom = payload["keyboard"]["content"]["rows"][0]["buttons"]
        assert callback["action"]["type"] == 1
        assert custom["action"]["type"] == 2
        assert custom["action"]["data"] == "/test "
        assert custom["action"]["reply"] is True
        assert custom["action"]["enter"] is False
        assert deletions == []

        source = cards.clicked(scope, destination, callback["id"])
        assert source is not None
        assert cards.clicked(scope, "different-conversation", callback["id"]) is None
        quoted = message("/test 7", private=scope == "private", ref="REFIDX_1")
        assert cards.submitted(quoted) == source
        other = message("/test 7", private=scope != "private", ref="REFIDX_1")
        assert cards.submitted(other) is None
        await client.acknowledge("interaction-id")
        await cards.send(
            scope,
            destination,
            Card("下一页", [[Button("返回", "back")]]),
            event_id="button-event",
            replace=source,
        )
        assert posts[-1][0] == path
        assert posts[-1][1]["event_id"] == "button-event"
        assert acknowledgements == [("/interactions/interaction-id", {"code": 0})]
        assert deletions == [(f"{path}/ROBOT1.0_1", "true", 2)]
        assert cards.clicked(scope, destination, callback["id"]) is None


@pytest.mark.parametrize(
    ("token_status", "api_status", "code"),
    [
        (503, 200, 0),
        (200, 401, 11244),
        (200, 403, 112),
        (200, 429, 22009),
    ],
)
async def test_http_errors_are_bounded_and_redact_server_details(
    token_status, api_status, code
):
    token_calls = 0

    async def token(_):
        nonlocal token_calls
        token_calls += 1
        return web.json_response(
            {
                "access_token": "sensitive-access-token",
                "expires_in": 1,
                "code": 0,
                "message": "secret-detail",
            },
            status=token_status,
        )

    async def api(_):
        return web.json_response(
            {"code": code, "message": "secret-detail"}, status=api_status
        )

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_route("*", "/{tail:.*}", api)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        with pytest.raises(QQError) as caught:
            await client.send("group", "group", "test")
    assert caught.value.status == (token_status if token_status != 200 else api_status)
    assert "secret" not in str(caught.value)
    assert token_calls == (2 if api_status == 401 else 1)


@pytest.mark.parametrize("disconnect", [7, 9, 4009])
async def test_gateway_identify_heartbeat_resume_and_invalid_session(disconnect):
    identities = []
    heartbeat = asyncio.Event()
    connections = 0

    async def token(_):
        return web.json_response({"access_token": "test-token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": f"ws://{request.host}/ws"})

    async def socket(request):
        nonlocal connections
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 50}})
        identity = await ws.receive_json()
        identities.append(identity)
        connections += 1
        await ws.send_json(
            {
                "op": 0,
                "s": connections * 10,
                "t": "READY" if identity["op"] == 2 else "RESUMED",
                "d": {"session_id": "session-one"},
            }
        )
        await ws.send_json({"op": 0, "t": "IGNORED_EVENT", "d": {}})
        await ws.send_json({"op": 1})
        await ws.send_json(make_event("first" if connections == 1 else "second"))
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.TEXT:
                break
            if frame.json()["op"] == 1:
                heartbeat.set()
                await ws.send_json({"op": 11})
                if connections == 1:
                    if disconnect == 4009:
                        await ws.close(code=4009)
                    else:
                        await ws.send_json({"op": disconnect, "d": False})
                    break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_get("/gateway/bot", gateway)
    app.router.add_get("/ws", socket)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        events = client.events()
        try:
            assert (await asyncio.wait_for(anext(events), 3))["d"]["content"] == "first"
            assert client.ready.is_set()
            assert (await asyncio.wait_for(anext(events), 3))["d"][
                "content"
            ] == "second"
            await asyncio.wait_for(heartbeat.wait(), 1)
        finally:
            await events.aclose()
        assert not client.ready.is_set()
    assert identities[0] == {
        "op": 2,
        "d": {
            "token": "QQBot test-token",
            "intents": (1 << 25) | (1 << 26),
            "shard": [0, 1],
        },
    }
    assert identities[1]["op"] == (6 if disconnect == 7 else 2)
    if disconnect == 7:
        assert identities[1]["d"]["session_id"] == "session-one"


async def test_gateway_reconnects_on_bad_response_and_missing_heartbeat_ack(caplog):
    connections = 0
    sockets = []

    async def token(_):
        return web.json_response({"access_token": "test-token", "expires_in": 7200})

    async def gateway(request):
        nonlocal connections
        connections += 1
        if connections == 1:
            return web.json_response({"code": 1}, status=503)
        return web.json_response({"url": f"ws://{request.host}/ws"})

    async def socket(request):
        ws = web.WebSocketResponse()
        sockets.append(ws)
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 20}})
        await ws.receive_json()
        await ws.send_json(
            {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session"}}
        )
        if len(sockets) > 1:
            await ws.send_json(make_event("recovered"))
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_get("/gateway/bot", gateway)
    app.router.add_get("/ws", socket)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        events = client.events()
        try:
            assert (await asyncio.wait_for(anext(events), 6))["d"][
                "content"
            ] == "recovered"
        finally:
            await events.aclose()
    assert sockets[0].closed
    assert "reconnecting" in caplog.text


async def test_gateway_binary_close_reconnects():
    count = 0

    async def token(_):
        return web.json_response({"access_token": "test-token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": f"ws://{request.host}/ws"})

    async def socket(request):
        nonlocal count
        count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 10000}})
        await ws.receive_json()
        if count == 1:
            await ws.send_bytes(b"unexpected binary")
        else:
            await ws.send_json(make_event("reconnected"))
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_get("/gateway/bot", gateway)
    app.router.add_get("/ws", socket)
    async with serve(app) as url, aiohttp.ClientSession() as http:
        client = QQClient(http, "app", "secret", api_url=url, token_url=url + "/token")
        events = client.events()
        try:
            assert (await asyncio.wait_for(anext(events), 3))["d"][
                "content"
            ] == "reconnected"
        finally:
            await events.aclose()
