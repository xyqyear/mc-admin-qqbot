import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

log = logging.getLogger(__name__)


class McAdminError(RuntimeError):
    pass


class McAdminUnavailable(McAdminError):
    pass


class McAdminResponseError(McAdminError):
    def __init__(self, status_code: int, detail: Any = None) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"mc-admin HTTP {status_code}")


class OnlinePlayer(BaseModel):
    name: str
    uuid: str
    player_db_id: int


class ServerMetadata(BaseModel):
    id: str
    game_port: int = Field(alias="gamePort")


class ServerOverview(ServerMetadata):
    status: str
    online_players: list[OnlinePlayer]


class PlayerDetail(BaseModel):
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


class ServerMaintenance(BaseModel):
    active: bool = False
    kind: str | None = None


class ServerObservation(BaseModel):
    status: StatusSample
    maintenance: ServerMaintenance = Field(default_factory=ServerMaintenance)


class Monitoring(BaseModel):
    servers: dict[str, ServerObservation]

    def status(self, server_id: str, *, after: datetime | None = None) -> str:
        observation = self.servers.get(server_id)
        if observation is None:
            raise McAdminUnavailable("mc-admin server observation is missing")
        return observation.status.current(after=after)


class RconResult(BaseModel):
    output: str


class FileContent(BaseModel):
    content: str


class PlayerBan(BaseModel):
    name: str = Field(min_length=1)
    expires: str = "forever"


class AcceptedTask(BaseModel):
    task_id: str


class TaskState(BaseModel):
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    ended_at: datetime | None = None


class ServerTask(TaskState):
    task_id: str
    task_type: str
    created_at: datetime
    progress: float | None = None

    @property
    def active(self) -> bool:
        return self.status in {"pending", "running"}


class ServerTasks(BaseModel):
    tasks: list[ServerTask]


class RestorationScope(BaseModel):
    kind: str


class Restoration(BaseModel):
    status: str
    scope: RestorationScope | None = None
    is_rollback: bool = False
    operation_id: str | None = None


class Restorations(BaseModel):
    restorations: list[Restoration]


class EventPlayer(BaseModel):
    name: str
    uuid: str | None = None
    player_db_id: int | None = None


class ChatEvent(BaseModel):
    type: Literal["chat"]
    cursor: str
    server_id: str
    timestamp: datetime
    player: EventPlayer
    message: str


class HeartbeatFrame(BaseModel):
    type: Literal["heartbeat"]
    timestamp: datetime


class StreamResetFrame(BaseModel):
    type: Literal["stream_reset"]
    reason: Literal["cursor_too_old", "invalid_cursor"]


class UnknownFrame(BaseModel):
    type: str


type EventFrame = ChatEvent | HeartbeatFrame | StreamResetFrame | UnknownFrame
metadata_adapter = TypeAdapter(list[ServerMetadata])
players_adapter = TypeAdapter(list[PlayerDetail])
bans_adapter = TypeAdapter(list[PlayerBan])
ResponseModel = TypeVar("ResponseModel")


class McAdminClient:
    def __init__(
        self, http: aiohttp.ClientSession, base_url: str, master_token: str
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {master_token}"}

    @staticmethod
    def validate(data: Any, validator: Callable[[Any], ResponseModel]) -> ResponseModel:
        try:
            return validator(data)
        except ValidationError as exc:
            raise McAdminResponseError(200, "Invalid response") from exc

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self.http.request(
                method, self.base_url + path, headers=self.headers, json=body
            ) as response:
                if response.status == 204:
                    return None
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError) as exc:
                    raise McAdminResponseError(response.status) from exc
                if response.status >= 400:
                    raise McAdminResponseError(response.status, data)
                return data
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise McAdminUnavailable("mc-admin is unavailable") from exc

    async def servers(self) -> list[ServerMetadata]:
        return self.validate(
            await self.request("GET", "/servers/"), metadata_adapter.validate_python
        )

    async def overview(
        self, visible: Callable[[str], bool] | None = None
    ) -> list[ServerOverview]:
        metadata = [
            server
            for server in await self.servers()
            if visible is None or visible(server.id)
        ]
        if not metadata:
            return []
        monitoring = await self.monitoring()

        async def describe(server: ServerMetadata) -> ServerOverview:
            status = monitoring.status(server.id)
            return ServerOverview(
                id=server.id,
                gamePort=server.game_port,
                status=status,
                online_players=await self.online_players(server.id)
                if status == "HEALTHY"
                else [],
            )

        return list(await asyncio.gather(*(describe(server) for server in metadata)))

    async def monitoring(self) -> Monitoring:
        return self.validate(
            await self.request("GET", "/monitoring"), Monitoring.model_validate
        )

    async def online_players(self, server_id: str) -> list[OnlinePlayer]:
        data = await self.request(
            "GET", f"/servers/{quote(server_id, safe='')}/online-players"
        )
        return [
            OnlinePlayer(name=p.current_name, uuid=p.uuid, player_db_id=p.player_db_id)
            for p in self.validate(data, players_adapter.validate_python)
        ]

    async def player(self, uuid: str) -> PlayerDetail:
        return self.validate(
            await self.request("GET", f"/players/uuid/{quote(uuid, safe='')}"),
            PlayerDetail.model_validate,
        )

    async def rcon(self, server_id: str, command: str) -> str:
        result = self.validate(
            await self.request(
                "POST",
                f"/servers/{quote(server_id, safe='')}/rcon",
                {"command": command},
            ),
            RconResult.model_validate,
        )
        log.info("RCON server=%s command=%s result=completed", server_id, command)
        return result.output

    async def banned_players(self, server_id: str) -> list[str]:
        server_path = f"/servers/{quote(server_id, safe='')}"
        try:
            response = await self.request(
                "GET",
                f"{server_path}/files/content?path=/banned-players.json",
            )
        except McAdminResponseError as exc:
            if exc.status_code == 404 and exc.detail == {"detail": "File not found"}:
                return []
            raise
        content = self.validate(response, FileContent.model_validate).content
        now = datetime.now(UTC)
        try:
            return [
                entry.name
                for entry in bans_adapter.validate_json(content)
                if entry.expires == "forever"
                or datetime.strptime(entry.expires, "%Y-%m-%d %H:%M:%S %z") > now
            ]
        except ValueError as exc:
            raise McAdminResponseError(200, "Invalid ban list") from exc

    async def send_message(
        self,
        server_id: str,
        message: str,
        *,
        target_player: str | None = None,
        color: str = "yellow",
    ) -> None:
        body: dict[str, Any] = {"message": message, "color": color}
        if target_player is not None:
            body["target_player"] = target_player
        await self.request(
            "POST", f"/servers/{quote(server_id, safe='')}/message", body
        )

    async def restart(self, server_id: str) -> str:
        return await self.operate(server_id, "restart")

    async def operate(self, server_id: str, action: str) -> str:
        result = self.validate(
            await self.request(
                "POST",
                f"/servers/{quote(server_id, safe='')}/operations",
                {"action": action},
            ),
            AcceptedTask.model_validate,
        )
        log.info(
            "%s server=%s task=%s result=accepted", action, server_id, result.task_id
        )
        return result.task_id

    async def server_tasks(self, server_id: str) -> list[ServerTask]:
        return self.validate(
            await self.request("GET", f"/tasks?server_id={quote(server_id, safe='')}"),
            ServerTasks.model_validate,
        ).tasks

    async def restoration(self, server_id: str, *, active: bool) -> Restoration | None:
        path = "/snapshots/restorations" + ("/active" if active else "")
        response = self.validate(
            await self.request(
                "GET", f"{path}?server_id={quote(server_id, safe='')}&limit=1"
            ),
            Restorations.model_validate,
        )
        return next(iter(response.restorations), None)

    async def task(self, task_id: str) -> TaskState:
        return self.validate(
            await self.request("GET", f"/tasks/{quote(task_id, safe='')}"),
            TaskState.model_validate,
        )

    async def status(self, server_id: str, *, after: datetime | None = None) -> str:
        return (await self.monitoring()).status(server_id, after=after)

    @staticmethod
    def parse_frame(raw: str | bytes) -> EventFrame:
        try:
            data = json.loads(raw)
            kind = data["type"]
            model = {
                "chat": ChatEvent,
                "heartbeat": HeartbeatFrame,
                "stream_reset": StreamResetFrame,
            }.get(kind, UnknownFrame)
            return model.model_validate(data)
        except (TypeError, KeyError, ValueError) as exc:
            raise McAdminResponseError(200, "Invalid event frame") from exc

    async def events(self, since: str | None) -> AsyncGenerator[EventFrame]:
        url = self.base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        try:
            async with self.http.ws_connect(
                url + "/events",
                params={"since": since} if since else None,
                headers=self.headers,
                timeout=aiohttp.ClientWSTimeout(ws_receive=35),  # pyright: ignore[reportCallIssue]
            ) as ws:
                async for frame in ws:
                    if frame.type == aiohttp.WSMsgType.TEXT:
                        yield self.parse_frame(frame.data)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise McAdminUnavailable("mc-admin event stream disconnected") from exc
