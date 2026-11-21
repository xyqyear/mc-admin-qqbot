import asyncio
from contextlib import suppress
from dataclasses import dataclass

from .cards import (
    Cards,
    SentCard,
    callback_button,
    markdown_text,
    paged_buttons,
    render_card,
)
from .database import Database
from .domain import ONLINE_STATUSES, UserInputError, resolve_selector
from .mcadmin import (
    McAdminClient,
    McAdminError,
    McAdminResponseError,
    McAdminUnavailable,
    OnlinePlayer,
    Restoration,
    ServerMetadata,
    ServerTask,
    StatusSample,
)
from .models import Scope
from .qq import Button, Card, QQMessage

MANAGE_COMMAND = "/管理"
MANAGE_CALLBACK = "manage:"
ACTIONS = {"start": "启动", "stop": "停服", "restart": "重启"}
LIFECYCLE = {
    "server_start": "启动",
    "server_up": "启动",
    "server_stop": "停服",
    "server_down": "下线",
    "server_restart": "重启",
    "server_create": "创建服务器",
    "server_remove": "删除服务器",
}
STOPPED = {"EXISTS", "CREATED"}
STATUS = {
    "EXISTS": "已停止",
    "CREATED": "已停止",
    "RUNNING": "运行中，等待游戏就绪",
    "STARTING": "启动中",
    "HEALTHY": "已就绪",
    "REMOVED": "不可用",
    "UNKNOWN": "等待状态更新",
}
TASK_STATUS = {
    "pending": "已受理",
    "running": "执行中",
    "completed": "已完成",
    "succeeded": "已完成",
    "failed": "失败，请在网页查看详情",
    "cancelled": "已取消",
    "interrupted": "已中断，请在网页核对",
}


def button(label: str, *target: str, primary: bool = False) -> Button:
    return callback_button(MANAGE_CALLBACK, label, *target, primary=primary)


def task_result(task: ServerTask, sample: StatusSample | None) -> str:
    if task.status == "running" and task.progress is not None:
        return f"执行中 · {task.progress:.0f}%"
    if task.status == "completed" and task.task_type in {
        "server_start",
        "server_up",
        "server_restart",
    }:
        if task.ended_at and sample:
            with suppress(McAdminUnavailable):
                if sample.current(after=task.ended_at) == "HEALTHY":
                    return "已完成 · 游戏已就绪"
        return "等待游戏就绪"
    return TASK_STATUS[task.status]


@dataclass(frozen=True)
class ServerState:
    status: str
    sample: StatusSample | None
    players: list[OnlinePlayer] | None
    maintenance: str | None
    restoration: Restoration | None

    @property
    def player_count(self) -> str:
        return "等待状态更新" if self.players is None else f"{len(self.players)} 人"

    def allows(self, action: str) -> bool:
        return self.maintenance is None and self.status in (
            STOPPED if action == "start" else ONLINE_STATUSES
        )


class Management:
    def __init__(self, database: Database, mc: McAdminClient, cards: Cards) -> None:
        self.database = database
        self.mc = mc
        self.cards = cards

    async def servers(
        self, scope: Scope, destination: str
    ) -> tuple[list[ServerMetadata], str | None]:
        servers = await self.mc.servers()
        async with self.database.sessions() as session:
            default = await self.database.default_server(session)
            if scope == "group":
                routes = await self.database.group_routes(session)
                servers = [
                    server
                    for server in servers
                    if routes.destination(server.id) == destination
                ]
        return sorted(
            servers, key=lambda server: (server.game_port, server.id)
        ), default

    async def command(self, message: QQMessage) -> Card:
        _, *args = message.content.split()
        if len(args) > 1:
            raise UserInputError("用法：/管理 [服务器]")
        servers, default = await self.servers(message.scope, message.destination)
        if not servers:
            raise UserInputError("当前会话没有可管理的服务器")
        if args:
            selector = args[0].removeprefix("/")
            server = resolve_selector(servers, selector) if selector else None
            if server is None:
                raise UserInputError("找不到可管理的服务器")
        else:
            server = next((item for item in servers if item.id == default), servers[0])
        return await self.home(server.id)

    async def state(self, server_id: str, *, players: bool = False) -> ServerState:
        monitoring, restoration = await asyncio.gather(
            self.mc.monitoring(), self.mc.restoration(server_id, active=True)
        )
        observation = monitoring.servers.get(server_id)
        sample = observation.status if observation else None
        status, maintenance = "UNKNOWN", None
        if observation:
            with suppress(McAdminUnavailable):
                status = observation.status.current()
            if observation.maintenance.active:
                kind = observation.maintenance.kind or ""
                maintenance = {
                    **LIFECYCLE,
                    "restore": "恢复",
                    "backup": "备份",
                    "recovery": "等待恢复核对",
                }.get(kind, "维护进行中")
        if restoration:
            maintenance = (
                "恢复任务已受理" if restoration.status == "pending" else "恢复进行中"
            )
        online = [] if status in STOPPED else None
        if players and status == "HEALTHY":
            with suppress(McAdminError):
                online = await self.mc.online_players(server_id)
        return ServerState(status, sample, online, maintenance, restoration)

    async def home(self, sid: str, notice: str = "") -> Card:
        state = await self.state(sid, players=True)
        return render_card(
            markdown_text(sid),
            [
                [
                    button("启停与重启", "operations", sid, primary=True),
                    button("维护任务", "tasks", sid),
                ],
                [
                    button("在线玩家", "players", sid, "0"),
                    button("切换服务器", "servers", sid, "0"),
                ],
                [button("刷新状态", "refresh", sid)],
            ],
            f"服务器状态 ｜ {STATUS[state.status]}",
            f"在线玩家 ｜ {state.player_count}",
            f"当前维护 ｜ {state.maintenance or '空闲'}",
            path=("管理",),
            notice=notice,
        )

    def server_picker(
        self, servers: list[ServerMetadata], current: str, page: str
    ) -> Card:
        items, index, nav = paged_buttons(
            servers, page, MANAGE_CALLBACK, "servers", current
        )
        rows = [
            [button(server.id, "home", server.id, primary=server.id == current)]
            for server in items
        ]
        selected = next((item for item in servers if item.id == current), None)
        if selected:
            nav.append(button("返回", "home", current))
        if nav:
            rows.append(nav)
        if not rows:
            return self.error("当前会话没有可管理的服务器")
        return render_card(
            f"选择服务器 {index}" if len(servers) > 4 else "选择服务器",
            rows,
            f"当前 ｜ {markdown_text(selected.id)}" if selected else "",
            path=("管理", "选择服务器"),
        )

    async def operations(self, sid: str, can_write: bool) -> Card:
        state = await self.state(sid, players=True)
        rows = []
        if can_write and state.allows("start"):
            rows.append([button("启动服务器", "submit", sid, "start", primary=True)])
        if can_write and state.allows("restart"):
            rows.append(
                [
                    button("重启", "confirm", sid, "restart", primary=True),
                    button("停服", "confirm", sid, "stop"),
                ]
            )
        rows.append([button("查看进度", "tasks", sid), button("返回", "home", sid)])
        return render_card(
            "启停与重启",
            rows,
            f"状态 ｜ {STATUS[state.status]}",
            f"在线玩家 ｜ {state.player_count}",
            f"当前维护 ｜ {state.maintenance}" if state.maintenance else "",
            "启停操作由机器人超级管理员执行。" if not can_write else "",
            path=("管理", markdown_text(sid), "启停与重启"),
        )

    async def confirmation(self, sid: str, action: str) -> Card:
        label = ACTIONS[action]
        state = await self.state(sid, players=True)
        if not state.allows(action):
            raise UserInputError("服务器状态已变化，请刷新后再操作")
        return render_card(
            f"确认{label}",
            [
                [
                    button(f"确认{label}", "submit", sid, action, primary=True),
                    button("取消", "operations", sid),
                ]
            ],
            f"在线玩家 ｜ {state.player_count}",
            "操作会中断当前游戏连接。",
            path=("管理", markdown_text(sid), "启停与重启", "确认"),
        )

    def task_card(self, sid: str, *lines: str, task_id: str = "") -> Card:
        return render_card(
            "维护任务",
            [
                [
                    button("刷新进度", "tasks", sid, task_id, primary=True),
                    button("打开启停", "operations", sid),
                    button("返回", "home", sid),
                ]
            ],
            *lines,
            path=("管理", markdown_text(sid), "维护任务"),
        )

    async def tasks(self, sid: str, task_id: str = "") -> Card:
        state, tasks = await asyncio.gather(self.state(sid), self.mc.server_tasks(sid))
        lifecycle = [task for task in tasks if task.task_type in LIFECYCLE]
        pinned = next((task for task in lifecycle if task.task_id == task_id), None)
        recent = pinned or max(
            lifecycle, key=lambda task: (task.active, task.created_at), default=None
        )
        lines = [f"服务器状态 ｜ {STATUS[state.status]}"]
        if recent:
            lines.append(
                f"{LIFECYCLE[recent.task_type]} ｜ {task_result(recent, state.sample)}"
            )
        if task_id and not pinned:
            lines.append("已受理任务暂不可见，请在网页核对")
        restoration = state.restoration
        if restoration is None:
            try:
                restoration = await self.mc.restoration(sid, active=False)
            except McAdminError:
                lines.append("历史恢复记录暂不可用")
        if restoration:
            task = next(
                (item for item in tasks if item.task_id == restoration.operation_id),
                None,
            )
            label = "快照恢复"
            if restoration.is_rollback:
                label = "回滚恢复"
            elif restoration.scope and restoration.scope.kind == "world":
                label = "世界恢复"
            progress = (
                task_result(task, state.sample)
                if task and task.active
                else TASK_STATUS[restoration.status]
            )
            lines.append(f"{label} ｜ {progress}")
        if (
            state.maintenance
            and not (recent and recent.active)
            and not state.restoration
        ):
            lines.append(f"当前维护 ｜ {state.maintenance}")
        if len(lines) == 1:
            lines.append("当前没有维护任务记录")
        return self.task_card(sid, *lines, task_id=task_id)

    async def player_page(self, sid: str, page: str) -> Card:
        state = await self.state(sid, players=True)
        names = sorted(
            (player.name for player in state.players or []), key=str.casefold
        )
        items, index, nav = paged_buttons(
            names, page, MANAGE_CALLBACK, "players", sid, size=20
        )
        rows = [
            [
                button("刷新玩家", "players", sid, page, primary=True),
                button("返回", "home", sid),
            ]
        ]
        if nav:
            rows.insert(0, nav)
        if state.status == "HEALTHY":
            rows.append(
                [
                    Button("白名单图片", f"/whitelist list /{sid}", command=True),
                    Button("封禁名单图片", f"/banlist /{sid}", command=True),
                ]
            )
        return render_card(
            f"在线玩家 {index}" if nav else "在线玩家",
            rows,
            f"在线玩家 ｜ {state.player_count}",
            "、".join(map(markdown_text, items))
            if items
            else "当前无人在线"
            if state.players == []
            else "",
            path=("管理", markdown_text(sid), "在线玩家"),
        )

    async def submit(self, sid: str, action: str, source: SentCard) -> Card:
        if not (await self.state(sid)).allows(action):
            raise UserInputError("服务器正在维护或状态已变化，请刷新后再操作")
        self.cards.forget(source)
        try:
            task_id = await self.mc.operate(sid, action)
        except McAdminResponseError as exc:
            if exc.status_code in {409, 423}:
                return self.task_card(sid, "服务器当前有冲突的操作，请刷新任务状态")
            if 400 <= exc.status_code < 500:
                return self.task_card(sid, "请求被服务器拒绝，请在网页核对")
            return self.task_card(sid, "请求结果待核对，请刷新任务状态")
        except McAdminUnavailable:
            return self.task_card(sid, "请求结果待核对，请刷新任务状态")
        return self.task_card(sid, f"{ACTIONS[action]} ｜ 已受理", task_id=task_id)

    def error(self, notice: str) -> Card:
        return render_card(
            "服务器管理",
            [[button("切换服务器", "servers", "", "0")]],
            notice=notice,
        )

    async def click(self, action: list[str], source: SentCard, can_write: bool) -> Card:
        page, sid, *args = action
        servers, _ = await self.servers(source.scope, source.destination)
        if page == "servers":
            return self.server_picker(servers, sid, args[0])
        if not any(server.id == sid for server in servers):
            raise UserInputError("找不到可管理的服务器")
        match page:
            case "home" | "refresh":
                return await self.home(
                    sid, "已刷新服务器状态" if page == "refresh" else ""
                )
            case "operations":
                return await self.operations(sid, can_write)
            case "tasks":
                return await self.tasks(sid, *args)
            case "players":
                return await self.player_page(sid, args[0])
            case "confirm" if can_write:
                return await self.confirmation(sid, args[0])
            case "submit" if can_write:
                return await self.submit(sid, args[0], source)
            case _:
                raise UserInputError("启停操作由机器人超级管理员执行")
