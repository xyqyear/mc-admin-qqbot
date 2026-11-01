import json
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlencode

from nonebot.drivers import (
    Driver,
    HTTPClientMixin,
    Request,
    WebSocketClientMixin,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class McAdminError(RuntimeError):
    pass


class McAdminUnavailable(McAdminError):
    pass


class McAdminResponseError(McAdminError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class OnlinePlayer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    uuid: str
    player_db_id: int


class ServerMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    game_port: int = Field(alias="gamePort")


class ServerOverview(ServerMetadata):
    status: str
    online_players: list[OnlinePlayer]


class PlayerDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    player_db_id: int
    uuid: str
    current_name: str


class StatusSample(BaseModel):
    value: (
        Literal["REMOVED", "EXISTS", "CREATED", "RUNNING", "STARTING", "HEALTHY"] | None
    )
    quality: Literal["ready", "pending", "stale", "unavailable"]
    sampled_at: datetime | None

    def current(self, *, after: datetime | None = None) -> str:
        if (
            self.quality != "ready"
            or self.value is None
            or self.sampled_at is None
            or self.sampled_at.tzinfo is None
            or (datetime.now(UTC) - self.sampled_at).total_seconds() > 6
            or (after is not None and self.sampled_at <= after)
        ):
            raise McAdminUnavailable("mc-admin status observation is not current")
        return self.value


class ServerObservation(BaseModel):
    status: StatusSample


class Monitoring(BaseModel):
    servers: dict[str, ServerObservation]

    def status(self, server_id: str, *, after: datetime | None = None) -> str:
        observation = self.servers.get(server_id)
        if observation is None:
            raise McAdminUnavailable("mc-admin server observation is missing")
        return observation.status.current(after=after)


class RconResult(BaseModel):
    output: str


class EventPlayer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    uuid: str | None = None
    player_db_id: int | None = None


class ChatEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["chat"]
    cursor: str
    server_id: str
    timestamp: datetime
    player: EventPlayer
    message: str


class HeartbeatFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["heartbeat"]
    timestamp: datetime


class StreamResetFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["stream_reset"]
    reason: Literal["cursor_too_old", "invalid_cursor"]


class UnknownFrame(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str


EventFrame = ChatEvent | HeartbeatFrame | StreamResetFrame | UnknownFrame
metadata_adapter = TypeAdapter(list[ServerMetadata])
players_adapter = TypeAdapter(list[PlayerDetail])
ResponseModel = TypeVar("ResponseModel")


class McAdminClient:
    def __init__(
        self,
        driver: Driver,
        base_url: str,
        master_token: str,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(driver, HTTPClientMixin):
            raise RuntimeError("The configured driver must support HTTP clients")
        if not isinstance(driver, WebSocketClientMixin):
            raise RuntimeError("The configured driver must support WebSocket clients")
        self.driver = driver
        self.base_url = base_url.rstrip("/")
        self.master_token = master_token
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.master_token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _validate_response(
        data: Any, validator: Callable[[Any], ResponseModel]
    ) -> ResponseModel:
        try:
            return validator(data)
        except ValidationError as exc:
            raise McAdminResponseError(
                200, "mc-admin returned an invalid response"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        request = Request(
            method,
            self._url(path),
            headers=self.headers,
            timeout=self.timeout,
            content=(
                None
                if body is None
                else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            ),
        )
        try:
            response = await self.driver.request(request)  # type: ignore[attr-defined]
        except Exception as exc:
            raise McAdminUnavailable("mc-admin is temporarily unavailable") from exc

        content = response.content
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeError as exc:
                raise McAdminResponseError(
                    response.status_code, "mc-admin returned invalid UTF-8"
                ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            detail = f"mc-admin returned HTTP {response.status_code}"
            if content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and isinstance(
                        parsed.get("detail"), str
                    ):
                        detail = parsed["detail"]
                except (TypeError, ValueError):
                    pass
            raise McAdminResponseError(response.status_code, detail)
        if not content:
            return None
        try:
            return json.loads(content)
        except (TypeError, ValueError) as exc:
            raise McAdminResponseError(
                response.status_code, "mc-admin returned invalid JSON"
            ) from exc

    async def overview(self) -> list[ServerOverview]:
        data = await self._request("GET", "/servers/")
        metadata = self._validate_response(data, metadata_adapter.validate_python)
        monitoring = await self.monitoring()
        servers = []
        for server in metadata:
            status = monitoring.status(server.id)
            players = []
            if status in {"RUNNING", "STARTING", "HEALTHY"}:
                data = await self._request(
                    "GET", f"/servers/{quote(server.id, safe='')}/online-players"
                )
                players = [
                    OnlinePlayer(
                        name=player.current_name,
                        uuid=player.uuid,
                        player_db_id=player.player_db_id,
                    )
                    for player in self._validate_response(
                        data, players_adapter.validate_python
                    )
                ]
            servers.append(
                ServerOverview(
                    id=server.id,
                    name=server.name,
                    gamePort=server.game_port,
                    status=status,
                    online_players=players,
                )
            )
        return servers

    async def monitoring(self) -> Monitoring:
        data = await self._request("GET", "/monitoring")
        return self._validate_response(data, Monitoring.model_validate)

    async def player(self, uuid: str) -> PlayerDetail:
        data = await self._request("GET", f"/players/uuid/{quote(uuid, safe='')}")
        return self._validate_response(data, PlayerDetail.model_validate)

    async def rcon(self, server_id: str, command: str) -> str:
        data = await self._request(
            "POST",
            f"/servers/{quote(server_id, safe='')}/rcon",
            {"command": command},
        )
        return self._validate_response(data, RconResult.model_validate).output

    async def send_message(
        self,
        server_id: str,
        message: str,
        *,
        target_player: str | None = None,
        color: str = "yellow",
    ) -> None:
        await self._request(
            "POST",
            f"/servers/{quote(server_id, safe='')}/message",
            {
                "message": message,
                "target_player": target_player,
                "color": color,
            },
        )

    async def restart(self, server_id: str) -> None:
        await self._request(
            "POST",
            f"/servers/{quote(server_id, safe='')}/operations",
            {"action": "restart"},
        )

    async def status(self, server_id: str, *, after: datetime | None = None) -> str:
        return (await self.monitoring()).status(server_id, after=after)

    @staticmethod
    def parse_frame(raw: str | bytes) -> EventFrame:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
            frame_type = data.get("type") if isinstance(data, dict) else None
            if frame_type == "chat":
                return ChatEvent.model_validate(data)
            if frame_type == "heartbeat":
                return HeartbeatFrame.model_validate(data)
            if frame_type == "stream_reset":
                return StreamResetFrame.model_validate(data)
            return UnknownFrame.model_validate(data)
        except (TypeError, ValueError, ValidationError) as exc:
            raise McAdminResponseError(
                200, "mc-admin sent an invalid event frame"
            ) from exc

    async def events(self, since: str | None) -> AsyncGenerator[EventFrame]:
        ws_url = self.base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        query = "" if since is None else f"?{urlencode({'since': since})}"
        request = Request(
            "GET",
            f"{ws_url}/events{query}",
            headers={"Authorization": f"Bearer {self.master_token}"},
            timeout=35.0,
        )
        try:
            async with self.driver.websocket(request) as websocket:  # type: ignore[attr-defined]
                while True:
                    yield self.parse_frame(await websocket.receive())
        except McAdminResponseError:
            raise
        except Exception as exc:
            raise McAdminUnavailable("mc-admin event stream disconnected") from exc
