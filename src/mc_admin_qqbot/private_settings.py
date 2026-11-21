from collections import Counter
from dataclasses import dataclass
from typing import Literal

import aiohttp

from .binding import Bindings
from .cards import callback_button, markdown_text, paged_buttons, render_card
from .database import Database, GroupRoutes
from .domain import UserInputError
from .mcadmin import McAdminClient, ServerMetadata
from .models import BotPreferences, ServerGroupRule
from .qq import Button, Card, QQClient, QQError

CALLBACK_PREFIX = "settings:"
SETTINGS_HELP = (
    "/设置 管理服务器群组、默认服务器和系统设置\n/botname refresh 刷新机器人名"
)


@dataclass(frozen=True)
class SystemOption:
    label: str
    field: Literal["restart_timeout_seconds", "restart_poll_interval_seconds"]
    unit: str
    seconds: int
    presets: tuple[int, ...]

    def current(self, preferences: BotPreferences) -> int:
        return getattr(preferences, self.field) // self.seconds


OPTIONS = {
    "timeout": SystemOption(
        "重启等待上限", "restart_timeout_seconds", "分钟", 60, (5, 10, 20)
    ),
    "poll": SystemOption(
        "状态查询间隔", "restart_poll_interval_seconds", "秒", 1, (3, 5, 10)
    ),
}


def button(label: str, *target: str, primary: bool = False) -> Button:
    return callback_button(CALLBACK_PREFIX, label, *target, primary=primary)


def card(
    title: str,
    rows: list[list[Button]],
    *lines: str,
    path: tuple[str, ...] = (),
    notice: str = "",
) -> Card:
    return render_card(
        title, rows, *lines, path=("设置", *path) if path else (), notice=notice
    )


def page_items[T](
    items: list[T], requested: str, *target: str
) -> tuple[list[T], str, list[Button]]:
    try:
        return paged_buttons(items, requested, CALLBACK_PREFIX, *target)
    except ValueError as exc:
        raise UserInputError("此按钮已失效，请重新打开 /设置") from exc


def group_label(routes: GroupRoutes, openid: str | None) -> str:
    if not openid:
        return "未设置"
    group = routes.groups.get(openid)
    name = group.name if group and group.name else f"群聊 …{openid[-6:]}"
    return name if group and group.joined else f"{name}（已退出）"


class PrivateSettings:
    def __init__(
        self, database: Database, qq: QQClient, mc: McAdminClient, bindings: Bindings
    ) -> None:
        self.database = database
        self.qq = qq
        self.mc = mc
        self.bindings = bindings

    async def refresh_bot_name(self) -> str:
        try:
            name = await self.qq.bot_name()
        except (QQError, aiohttp.ClientError, TimeoutError):
            name = None
        if not name:
            return "暂时无法刷新机器人名，稍后可以重试"
        self.bindings.bot_name = name
        return f"已刷新机器人名 · {markdown_text(name)}"

    async def command(self, content: str) -> Card | None:
        name, *args = content.split()
        if name == "/botname" and args == ["refresh"]:
            return await self.home(await self.refresh_bot_name())
        if name != "/设置":
            return None
        if not args:
            return await self.home()
        for key, option in OPTIONS.items():
            if args[0] == option.label:
                if len(args) == 1:
                    return await self.system(key)
                return await self.save_option(key, " ".join(args[1:]))
        raise UserInputError("请发送 /设置 打开设置页")

    async def click(self, action: list[str]) -> Card:
        match action:
            case ["home"]:
                return await self.home()
            case ["refresh"]:
                return await self.home(await self.refresh_bot_name())
            case ["servers", page]:
                return await self.servers(page)
            case ["default", page]:
                return await self.default_server(page)
            case ["choose-default", server, page]:
                return await self.save_default_server(server, page)
            case ["choose", server, server_page, page]:
                return await self.groups(server, page, server_page)
            case ["bind", server, group, page]:
                return await self.save_group(server, group, page)
            case ["exclude" | "reset" as operation, server, page]:
                return await self.save_group(server, "", page, operation)
            case ["system"]:
                return await self.system()
            case ["edit", key] if key in OPTIONS:
                return await self.system(key)
            case ["save", key, value] if key in OPTIONS:
                return await self.save_option(key, value)
            case _:
                raise UserInputError("此按钮已失效，请重新打开 /设置")

    async def home(self, notice: str = "") -> Card:
        async with self.database.sessions() as session:
            routes = await self.database.group_routes(session)
            default_server = await self.database.default_server(session)
        return card(
            "机器人设置",
            [
                [
                    button("服务器群组", "servers", "0", primary=True),
                    button("设置默认群", "choose", "", "0", "0"),
                ],
                [
                    button("默认服务器", "default", "0"),
                    button("系统设置", "system"),
                ],
                [button("刷新机器人名", "refresh")],
            ],
            f"默认群 ｜ {markdown_text(group_label(routes, routes.fallback))}",
            f"默认服务器 ｜ {markdown_text(default_server or '自动选择')}",
            f"机器人名 ｜ {markdown_text(self.bindings.bot_name)}",
            notice=notice,
        )

    async def default_server(
        self, page: str, notice: str = "", metadata: list[ServerMetadata] | None = None
    ) -> Card:
        if metadata is None:
            metadata = await self.mc.servers()
        async with self.database.sessions() as session:
            default = await self.database.default_server(session)
        items, index, nav = page_items(
            sorted(metadata, key=lambda server: (server.game_port, server.id)),
            page,
            "default",
        )
        rows = [
            [
                button(
                    f"✓ {server.id}" if server.id == default else server.id,
                    "choose-default",
                    server.id,
                    page,
                    primary=server.id == default,
                )
            ]
            for server in items
        ]
        rows.append(
            [
                button("自动选择", "choose-default", "", page, primary=default is None),
                button("返回设置", "home"),
                *nav,
            ]
        )
        return card(
            f"默认服务器 {index}" if nav else "默认服务器",
            rows,
            f"当前 ｜ {markdown_text(default or '自动选择')}",
            "" if items else "暂无服务器",
            path=("默认服务器",),
            notice=notice,
        )

    async def save_default_server(self, server: str, page: str) -> Card:
        metadata = await self.mc.servers()
        if server and all(item.id != server for item in metadata):
            return await self.default_server(
                page, "服务器已不存在，请重新选择", metadata
            )
        await self.database.save_default_server(server or None)
        label = markdown_text(server or "自动选择")
        return await self.default_server(page, f"已更新默认服务器 · {label}", metadata)

    async def servers(
        self,
        page: str = "0",
        notice: str = "",
        metadata: list[ServerMetadata] | None = None,
    ) -> Card:
        if metadata is None:
            metadata = await self.mc.servers()
        async with self.database.sessions() as session:
            routes = await self.database.group_routes(session)
        items, index, nav = page_items(
            sorted(metadata, key=lambda server: (server.game_port, server.id)),
            page,
            "servers",
        )
        lines = [f"默认群 ｜ {markdown_text(group_label(routes, routes.fallback))}"]
        rows = []
        for server in items:
            server_id = server.id
            group = routes.rules.get(server_id)
            if server_id not in routes.rules:
                label = "默认群" if routes.destination(server_id) else "待分配"
                extras = [button("排除", "exclude", server_id, page)]
            elif group is None:
                label = "排除"
                extras = [button("恢复默认", "reset", server_id, page)]
            else:
                label = group_label(routes, group)
                extras = [
                    button("解绑到默认", "reset", server_id, page),
                    button("排除", "exclude", server_id, page),
                ]
            lines.append(f"{markdown_text(server_id)} ｜ {markdown_text(label)}")
            action = "改绑" if group else "绑定"
            rows.append(
                [
                    button(
                        f"{server_id} · {action}",
                        "choose",
                        server_id,
                        page,
                        "0",
                        primary=True,
                    ),
                    *extras,
                ]
            )
        if not items:
            lines.append("暂无服务器")
        rows.append([button("返回设置", "home"), *nav])
        return card(
            f"服务器群组 {index}", rows, *lines, path=("服务器群组",), notice=notice
        )

    async def groups(self, server: str, page: str, server_page: str) -> Card:
        async with self.database.sessions() as session:
            routes = await self.database.group_routes(session)
        joined = [openid for openid, group in routes.groups.items() if group.joined]
        labels = {openid: group_label(routes, openid) for openid in joined}
        label_counts = Counter(labels.values())
        items, _, nav = page_items(
            sorted(joined, key=lambda openid: (labels[openid].casefold(), openid)),
            page,
            "choose",
            server,
            server_page,
        )
        lines = []
        rows = []
        for openid in items:
            label = labels[openid]
            suffix = f"…{openid[-6:]}"
            lines.append(f"{markdown_text(label)} ｜ OpenID {suffix}")
            label = label[:14] + "…" if len(label) > 15 else label
            if label_counts[labels[openid]] > 1:
                label = f"{label} {suffix}"
            if openid == routes.fallback:
                label += " · 默认群"
            rows.append(
                [button(label, "bind", server, openid, server_page, primary=True)]
            )
        if not items:
            lines.append("暂无已发现的群。请在机器人所在的群里 @它发一条消息。")
        back = (
            [button("返回", "servers", server_page)]
            if server
            else [button("返回", "home")]
        )
        if not server and routes.fallback:
            back.append(button("清除默认群", "bind", "", "", "0"))
        rows.append([*back, *nav])
        server = markdown_text(server)
        if server:
            return card(
                f"选择 {server} 的群",
                rows,
                *lines,
                path=("服务器群组", server, "选择群"),
            )
        return card("选择默认群", rows, *lines, path=("默认群",))

    async def save_group(
        self, server: str, group: str, page: str, operation: str = "bind"
    ) -> Card:
        metadata = await self.mc.servers() if server else []
        if server and all(item.id != server for item in metadata):
            raise UserInputError("服务器已不存在，请重新打开 /设置")
        async with self.database.sessions.begin() as session:
            routes = await self.database.group_routes(session)
            if group and (
                group not in routes.groups or not routes.groups[group].joined
            ):
                raise UserInputError("机器人已不在该群，请重新选择")
            if server:
                if operation == "reset":
                    rule = await session.get(
                        ServerGroupRule, (self.database.app_id, server)
                    )
                    if rule:
                        await session.delete(rule)
                else:
                    await session.merge(
                        ServerGroupRule(
                            app_id=self.database.app_id,
                            server_id=server,
                            group_openid=group or None,
                        )
                    )
            else:
                preferences = await self.database.preferences(session)
                preferences.fallback_group = group or None
        label = markdown_text(group_label(routes, group))
        if not server:
            return await self.home(
                f"默认群已更新为 {label}" if group else "已清除默认群"
            )
        status = {
            "bind": f"已绑定到 {label}",
            "exclude": "已排除",
            "reset": "已恢复默认路由",
        }[operation]
        return await self.servers(page, f"{markdown_text(server)} {status}", metadata)

    async def system(self, key: str = "", notice: str = "") -> Card:
        async with self.database.sessions() as session:
            preferences = await self.database.preferences(session)
        if not key:
            return card(
                "系统设置",
                [
                    [
                        button("修改等待上限", "edit", "timeout", primary=True),
                        button("修改查询间隔", "edit", "poll"),
                    ],
                    [button("返回设置", "home")],
                ],
                *(
                    f"{option.label} ｜ {option.current(preferences)} {option.unit}"
                    for option in OPTIONS.values()
                ),
                path=("系统设置",),
                notice=notice,
            )
        option = OPTIONS[key]
        value = option.current(preferences)
        return card(
            option.label,
            [
                [
                    button(
                        f"{preset} {option.unit}",
                        "save",
                        key,
                        str(preset),
                        primary=value == preset,
                    )
                    for preset in option.presets
                ],
                [
                    Button("自定义", f"/设置 {option.label} ", command=True),
                    button("返回", "system"),
                ],
            ],
            f"当前 ｜ {value} {option.unit}",
            path=("系统设置", option.label),
            notice=notice,
        )

    async def save_option(self, key: str, raw: str) -> Card:
        option = OPTIONS[key]
        if (
            not raw.isdecimal()
            or len(raw) > 10
            or not 0 < int(raw) * option.seconds < 2**31
        ):
            return await self.system(key, f"请输入有效的正整数，单位为{option.unit}")
        value = int(raw)
        async with self.database.sessions.begin() as session:
            preferences = await self.database.preferences(session)
            setattr(preferences, option.field, value * option.seconds)
        return await self.system(
            notice=f"已更新 {option.label} · {value} {option.unit}"
        )
