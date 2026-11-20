import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

from .models import Scope

log = logging.getLogger(__name__)
GROUP_MESSAGE_EVENTS = {
    "GROUP_MESSAGE_CREATE",
    "GROUP_AT_MESSAGE_CREATE",
}
MESSAGE_EVENTS = {*GROUP_MESSAGE_EVENTS, "C2C_MESSAGE_CREATE"}


class QQError(RuntimeError):
    def __init__(self, status: int, code: int = 0) -> None:
        self.status = status
        self.code = code
        self.retryable = status == 429 or status >= 500 or code == 22009
        super().__init__(f"QQ HTTP {status} code {code}")


@dataclass(frozen=True)
class SentMessage:
    id: str
    ref_idx: str | None


@dataclass(frozen=True)
class Button:
    label: str
    data: str
    primary: bool = False
    command: bool = False


@dataclass(frozen=True)
class Card:
    markdown: str
    rows: list[list[Button]]


@dataclass(frozen=True)
class QQMessage:
    id: str
    scope: Scope
    destination: str
    openid: str
    author: dict[str, Any]
    content: str
    msg_idx: str | None
    ref_msg_idx: str | None

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "QQMessage":
        data = event["d"]
        author = data["author"]
        group = data.get("group_openid")
        openid = author["member_openid"] if group else author["user_openid"]
        text = data.get("content", "").strip()
        for mention in data.get("mentions", []):
            if mention.get("is_you"):
                tag = f"<@{mention['id']}>"
                text = text.removeprefix(tag).lstrip()
        ext = {}
        for item in data.get("message_scene", {}).get("ext", []):
            key, _, value = item.partition("=")
            if key in {"msg_idx", "ref_msg_idx"}:
                ext[key] = value
        return cls(
            id=data["id"],
            scope="group" if group else "private",
            destination=group or openid,
            openid=openid,
            author=author,
            content=text,
            msg_idx=ext.get("msg_idx"),
            ref_msg_idx=ext.get("ref_msg_idx"),
        )


class QQClient:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        app_id: str,
        secret: str,
        *,
        api_url: str = "https://api.sgroup.qq.com",
        token_url: str = "https://bots.qq.com/app/getAppAccessToken",
    ) -> None:
        self.http = http
        self.app_id = app_id
        self.secret = secret
        self.api_url = api_url.rstrip("/")
        self.token_url = token_url
        self.ready = asyncio.Event()
        self._token = ""
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._session_id: str | None = None
        self._seq: int | None = None
        self._acked = True

    async def access_token(self) -> str:
        async with self._token_lock:
            if time.monotonic() < self._expires_at:
                return self._token
            async with self.http.post(
                self.token_url,
                json={"appId": self.app_id, "clientSecret": self.secret},
            ) as response:
                data = await response.json()
                if response.status != 200:
                    raise QQError(response.status, int(data.get("code", 0)))
                self._token = data["access_token"]
                ttl = float(data["expires_in"])
                self._expires_at = time.monotonic() + ttl - min(60, ttl / 10)
            return self._token

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            token = await self.access_token()
            async with self.http.request(
                method,
                self.api_url + path,
                json=body,
                headers={
                    "Authorization": f"QQBot {token}",
                    "X-Union-Appid": self.app_id,
                },
            ) as response:
                if response.status == 401 and attempt == 0:
                    attempt += 1
                    if self._token == token:
                        self._expires_at = 0
                    continue
                if response.status < 400 and not await response.read():
                    return {}
                data = await response.json()
                if response.status >= 400:
                    raise QQError(response.status, int(data.get("code", 0)))
                return data

    async def send(
        self,
        scope: Scope,
        destination: str,
        content: str | bytes,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> SentMessage:
        kind = "groups" if scope == "group" else "users"
        endpoint = f"/v2/{kind}/{destination}"
        if isinstance(content, bytes):
            upload = await self.request(
                "POST",
                f"{endpoint}/files",
                {
                    "file_type": 1,
                    "file_name": "players.png",
                    "file_data": base64.b64encode(content).decode("ascii"),
                    "srv_send_msg": False,
                },
            )
            body: dict[str, Any] = {
                "msg_type": 7,
                "media": {"file_info": upload["file_info"]},
            }
        else:
            body = {"msg_type": 0, "content": content}
        if msg_id:
            body.update(msg_id=msg_id, msg_seq=msg_seq)
        data = await self.request("POST", f"{endpoint}/messages", body)
        return SentMessage(data["id"], data.get("ext_info", {}).get("ref_idx"))

    async def group_state(self, group_openid: str) -> dict[str, Any]:
        return await self.request("GET", f"/v2/groups/{group_openid}/bot_state")

    async def bot_name(self) -> str | None:
        return (await self.request("GET", "/users/@me")).get("username")

    async def group_name(self, openid: str) -> str | None:
        info = await self.request("GET", f"/v2/groups/{quote(openid, safe='')}/info")
        return info.get("group_name")

    async def acknowledge(self, interaction_id: str, *, allowed: bool) -> None:
        await self.request(
            "PUT",
            f"/interactions/{quote(interaction_id, safe='')}",
            {"code": 0 if allowed else 4},
        )

    async def send_card(
        self,
        openid: str,
        card: Card,
        *,
        msg_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        rows = [
            {
                "buttons": [
                    {
                        "id": f"{row_index}-{button_index}",
                        "render_data": {
                            "label": button.label,
                            "visited_label": button.label,
                            "style": int(button.primary),
                        },
                        "action": {
                            "type": 2 if button.command else 1,
                            "permission": {"type": 2},
                            "data": button.data,
                            "enter": False,
                            "unsupport_tips": "请使用支持消息按钮的 QQ 客户端",
                        },
                    }
                    for button_index, button in enumerate(row)
                ]
            }
            for row_index, row in enumerate(card.rows)
        ]
        body: dict[str, Any] = {
            "msg_type": 2,
            "markdown": {"content": card.markdown},
            "keyboard": {"content": {"rows": rows}},
        }
        if event_id:
            body["event_id"] = event_id
        else:
            body["msg_id"] = msg_id
        await self.request("POST", f"/v2/users/{quote(openid, safe='')}/messages", body)

    async def _heartbeat(
        self, ws: aiohttp.ClientWebSocketResponse, interval: float
    ) -> None:
        while not ws.closed:
            if not self._acked:
                await ws.close()
                return
            self._acked = False
            await ws.send_json({"op": 1, "d": self._seq})
            await asyncio.sleep(interval)

    async def events(self) -> AsyncGenerator[dict[str, Any]]:
        delay = 1
        while True:
            pulse: asyncio.Task | None = None
            try:
                gateway = await self.request("GET", "/gateway/bot")
                async with self.http.ws_connect(
                    gateway["url"],
                    timeout=aiohttp.ClientWSTimeout(ws_receive=90),  # pyright: ignore[reportCallIssue]
                ) as ws:
                    hello = await ws.receive_json()
                    token = "QQBot " + await self.access_token()
                    if self._session_id:
                        identity = {
                            "op": 6,
                            "d": {
                                "token": token,
                                "session_id": self._session_id,
                                "seq": self._seq,
                            },
                        }
                    else:
                        identity = {
                            "op": 2,
                            "d": {
                                "token": token,
                                "intents": (1 << 25) | (1 << 26),
                                "shard": [0, 1],
                            },
                        }
                    await ws.send_json(identity)
                    self._acked = True
                    pulse = asyncio.create_task(
                        self._heartbeat(ws, hello["d"]["heartbeat_interval"] / 1000)
                    )
                    async for frame in ws:
                        if frame.type != aiohttp.WSMsgType.TEXT:
                            break
                        event = json.loads(frame.data)
                        op = event["op"]
                        if event.get("s") is not None:
                            self._seq = event["s"]
                        if op == 11:
                            self._acked = True
                        elif op == 1:
                            await ws.send_json({"op": 1, "d": self._seq})
                        elif op in {7, 9}:
                            if op == 9:
                                self._session_id = None
                            break
                        elif op == 0:
                            name = event.get("t")
                            if name == "READY":
                                self._session_id = event["d"]["session_id"]
                            if name in {"READY", "RESUMED"}:
                                self.ready.set()
                                delay = 1
                                log.info("QQ gateway connected")
                            elif (
                                name in MESSAGE_EVENTS
                                or name == "INTERACTION_CREATE"
                                or event["d"].get("group_openid")
                            ):
                                yield event
                    if ws.close_code in {4006, 4007, 4009}:
                        self._session_id = None
            except (aiohttp.ClientError, TimeoutError, OSError, QQError, ValueError):
                log.warning("QQ gateway unavailable; reconnecting")
            finally:
                self.ready.clear()
                if pulse is not None:
                    pulse.cancel()
                    await asyncio.gather(pulse, return_exceptions=True)
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)
