import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from .database import Database
from .domain import (
    ADMIN_HELP_TEXT,
    HELP_TEXT,
    UserInputError,
    format_server_list,
    is_admin,
    parse_targeted_text,
    translate_action,
    translate_whitelist,
    validate_player_name,
)
from .mcadmin import McAdminClient, McAdminResponseError, McAdminUnavailable
from .models import RestartWatch, User, utc_now
from .private_settings import SETTINGS_HELP
from .qq import QQMessage
from .roster import render_player_list

log = logging.getLogger(__name__)
ALIASES = {"ping": "list", "l": "list", "s": "say"}
COMMANDS = {"help", "list", "say", "whitelist", "ban", "unban", "banlist", "restart"}


class Commands:
    def __init__(self, database: Database, mc: McAdminClient) -> None:
        self.database = database
        self.mc = mc

    async def visibility(self, message: QQMessage) -> Callable[[str], bool]:
        if message.scope == "private":
            return lambda _: True
        async with self.database.sessions() as session:
            routes = await self.database.group_routes(session)
        return lambda server_id: routes.destination(server_id) == message.destination

    async def target(
        self, message: QQMessage, raw: str, *, chat: bool = False, restart: bool = False
    ):
        visible = await self.visibility(message)
        async with self.database.sessions() as session:
            default = await self.database.default_server(session)
        return parse_targeted_text(
            raw,
            await self.mc.overview(visible),
            default if default and visible(default) else None,
            strict_selector=not chat,
            require_explicit=restart,
        )

    async def handle(self, message: QQMessage, user: User) -> str | bytes | None:
        if message.ref_msg_idx:
            server_id = await self.database.route_for(message)
            if server_id:
                await self.say(message, user, message.content, routed_server=server_id)
                return None
        name, _, argument = message.content.partition(" ")
        if not name.startswith("/"):
            return None
        action = ALIASES.get(name[1:], name[1:])
        if action not in COMMANDS:
            return None
        admin = is_admin(message, user)
        privileged = action in {"ban", "unban", "restart"} or (
            action == "whitelist" and argument.split()[:1] in [["add"], ["remove"]]
        )
        if privileged and not admin:
            log.info("command user=%s action=%s result=denied", user.id, action)
            raise PermissionError
        log.info("command user=%s action=%s context=%s", user.id, action, message.scope)
        match action:
            case "help":
                help_text = f"{HELP_TEXT}\n\n{ADMIN_HELP_TEXT}" if admin else HELP_TEXT
                if message.scope == "private" and user.is_superuser:
                    help_text += f"\n\n{SETTINGS_HELP}"
                return help_text
            case "list":
                return format_server_list(
                    await self.mc.overview(await self.visibility(message))
                )
            case "say":
                await self.say(message, user, argument)
                return None
            case "whitelist":
                return await self.whitelist(message, argument)
            case "banlist":
                return await self.player_list(message, "banlist", argument)
            case "ban" | "unban":
                target = await self.target(message, argument)
                player = validate_player_name(target.text)
                command = "ban" if action == "ban" else "pardon"
                output = await self.mc.rcon(target.server.id, f"{command} {player}")
                return translate_action(action, player, output)
            case "restart":
                return await self.restart(message, user, argument)
        return None

    async def say(
        self,
        message: QQMessage,
        user: User,
        raw: str,
        *,
        routed_server: str | None = None,
    ) -> None:
        if not raw.strip():
            raise UserInputError("用法：/say <消息> [/<服务器>]")
        if user.player_uuid is None:
            raise UserInputError("请先在游戏内输入 \\\\bind，再私聊机器人提交验证码")
        if routed_server is None:
            target = await self.target(message, raw, chat=True)
            text, server_id = target.text, target.server.id
        else:
            if not (await self.visibility(message))(routed_server):
                raise UserInputError("该服务器在本群不可用")
            text, server_id = raw.strip(), routed_server
        if not text:
            raise UserInputError("消息不能为空")
        player = await self.mc.player(user.player_uuid)
        await self.mc.send_message(server_id, f"*<{player.current_name}> {text}")
        await self.database.record_route(message, server_id)
        log.info(
            "command user=%s action=say server=%s result=success", user.id, server_id
        )

    async def whitelist(self, message: QQMessage, raw: str) -> str | bytes:
        operation, _, rest = raw.strip().partition(" ")
        if operation == "list":
            return await self.player_list(message, "whitelist", rest)
        if operation not in {"add", "remove"}:
            raise UserInputError(
                "用法：/whitelist list [/<服务器>] 或 add/remove <玩家> [/<服务器>]"
            )
        target = await self.target(message, rest)
        player = validate_player_name(target.text)
        output = await self.mc.rcon(target.server.id, f"whitelist {operation} {player}")
        return translate_action(f"whitelist_{operation}", player, output)

    async def player_list(self, message: QQMessage, kind: str, raw: str) -> str | bytes:
        target = await self.target(message, raw)
        whitelist = kind == "whitelist"
        label = "白名单" if whitelist else "封禁名单"
        if target.text:
            usage = "/whitelist list" if whitelist else "/banlist"
            raise UserInputError(f"用法：{usage} [/<服务器>]")
        server_id = target.server.id
        if whitelist:
            names = translate_whitelist(await self.mc.rcon(server_id, "whitelist list"))
            if names is None:
                return "服务器返回的白名单无法解析"
        else:
            names = await self.mc.banned_players(server_id)
        return await asyncio.to_thread(render_player_list, label, server_id, names)

    async def restart(self, message: QQMessage, user: User, raw: str) -> str:
        target = await self.target(message, raw, restart=True)
        if target.text:
            raise UserInputError("用法：/restart /<服务器>")
        server_id = target.server.id
        try:
            async with self.database.sessions.begin() as session:
                preferences = await self.database.preferences(session)
                session.add(
                    RestartWatch(
                        server_id=server_id,
                        user_id=user.id,
                        scope=message.scope,
                        destination=message.destination,
                        reply_msg_id=message.id,
                        deadline=utc_now()
                        + timedelta(seconds=preferences.restart_timeout_seconds),
                    )
                )
        except IntegrityError as exc:
            raise UserInputError(f"[{server_id}] 已在重启监控中") from exc
        try:
            task_id = await self.mc.restart(server_id)
        except McAdminResponseError:
            async with self.database.sessions.begin() as session:
                await session.execute(
                    delete(RestartWatch).where(RestartWatch.server_id == server_id)
                )
            raise
        except McAdminUnavailable as exc:
            raise UserInputError("重启请求结果未确认，请在 mc-admin 中查看") from exc
        async with self.database.sessions.begin() as session:
            watch = await session.get(RestartWatch, server_id)
            assert watch is not None
            watch.task_id = task_id
        return f"[{server_id}] 已开始重启"
