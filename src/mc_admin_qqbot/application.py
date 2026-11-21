import asyncio
import logging
import secrets
import time

import aiohttp

from .binding import CODE_RE, Bindings
from .cards import Cards, callback_data
from .commands import Commands
from .database import Database
from .domain import ADMIN_ROLES, UserInputError, is_admin
from .management import MANAGE_CALLBACK, MANAGE_COMMAND, Management
from .mcadmin import McAdminClient, McAdminError
from .models import Scope, User
from .private_settings import CALLBACK_PREFIX, PrivateSettings
from .qq import MESSAGE_EVENTS, QQClient, QQError, QQMessage
from .relay import GameRelay
from .workers import consume_game, deliver_pending, watch_restarts

log = logging.getLogger(__name__)


class Application:
    def __init__(
        self,
        database: Database,
        qq: QQClient,
        mc: McAdminClient,
    ) -> None:
        self.database = database
        self.qq = qq
        self.mc = mc
        self.cards = Cards(qq)
        self.bindings = Bindings(database)
        self.commands = Commands(database, mc)
        self.private_settings = PrivateSettings(database, qq, mc, self.bindings)
        self.management = Management(database, mc, self.cards)
        self.relay = GameRelay(database, self.bindings)
        self.claim_token: str | None = None
        self._claim_lock = asyncio.Lock()
        self._seen: dict[str, None] = {}
        self._attempts: dict[int, tuple[float, int]] = {}
        self._group_refreshes: asyncio.Queue[str] = asyncio.Queue()

    async def startup(self) -> None:
        await self.database.initialize()
        self.claim_token = secrets.token_urlsafe(24)
        print(
            f"超级管理员绑定口令：{self.claim_token}\n"
            "请私聊机器人发送此口令。本次启动仅可使用一次，已有管理员权限保留。",
            flush=True,
        )
        await self.private_settings.refresh_bot_name()

    async def claim_admin(self, message: QQMessage, user: User) -> bool:
        async with self._claim_lock:
            if self.claim_token is None or not secrets.compare_digest(
                message.content.encode(), self.claim_token.encode()
            ):
                return False
            async with self.database.sessions.begin() as session:
                row = await session.get(User, user.id)
                assert row is not None
                row.is_superuser = True
            self.claim_token = None
            log.info("superuser claimed user=%s", user.id)
            return True

    def allow_code_attempt(self, user_id: int) -> bool:
        now = time.monotonic()
        started, attempts = self._attempts.get(user_id, (now, 0))
        if now - started >= 60:
            started, attempts = now, 0
        self._attempts[user_id] = (started, attempts + 1)
        return attempts < 5

    def remember(self, event_id: str) -> bool:
        if event_id in self._seen:
            return False
        self._seen[event_id] = None
        if len(self._seen) > 4096:
            self._seen.pop(next(iter(self._seen)))
        return True

    async def handle(self, event: dict) -> None:
        kind, data = event["t"], event["d"]
        if group := data.get("group_openid"):
            if await self.database.observe_group(group, kind, data.get("group_name")):
                self._group_refreshes.put_nowait(group)
        if kind == "INTERACTION_CREATE":
            await self.handle_interaction(event)
        elif kind in MESSAGE_EVENTS:
            await self.handle_message(QQMessage.from_event(event))

    async def handle_message(self, message: QQMessage) -> None:
        if message.author.get("bot") or not self.remember(message.id):
            return
        user = await self.database.observe(message)
        manage = message.content.split(maxsplit=1)[:1] == [MANAGE_COMMAND]
        try:
            if manage:
                if not is_admin(message, user):
                    return
                await self.cards.send(
                    message.scope,
                    message.destination,
                    await self.management.command(message),
                    msg_id=message.id,
                    owner=message.openid,
                )
                return
            if message.scope == "private":
                if await self.claim_admin(message, user):
                    response = "已绑定为超级管理员。发送 /设置 管理机器人。"
                elif CODE_RE.fullmatch(message.content):
                    if self.allow_code_attempt(user.id):
                        response = await self.bindings.confirm(user.id, message.content)
                        log.info("binding confirmation user=%s", user.id)
                    else:
                        response = "验证码尝试过于频繁，请一分钟后再试"
                elif user.is_superuser:
                    if message.content:
                        card = await self.private_settings.command(message.content)
                        if card is not None:
                            await self.cards.send(
                                message.scope,
                                message.destination,
                                card,
                                msg_id=message.id,
                                replace=self.cards.submitted(message),
                            )
                            return
                    response = await self.commands.handle(message, user)
                else:
                    return
            else:
                response = await self.commands.handle(message, user)
        except PermissionError:
            return
        except UserInputError as exc:
            response = str(exc)
        except McAdminError:
            log.warning("command user=%s result=mc-admin-unavailable", user.id)
            response = "mc-admin 暂时不可用，请稍后再试"
        except (QQError, aiohttp.ClientError, TimeoutError):
            log.warning("failed to send card user=%s", user.id)
            response = (
                "管理卡片暂时无法显示，请稍后再试"
                if manage
                else "设置卡片暂时无法显示，请稍后再试"
            )
        except Exception as exc:
            log.error("QQ command failed type=%s", type(exc).__name__)
            response = "命令执行失败，请稍后再试"
        if response:
            await self.database.reply(message, response)

    async def handle_interaction(self, event: dict) -> None:
        data = event["d"]
        resolved = data.get("data", {}).get("resolved", {})
        target = resolved.get("button_data", "")
        if not isinstance(target, str) or not target.startswith(
            (CALLBACK_PREFIX, MANAGE_CALLBACK)
        ):
            return
        manage = target.startswith(MANAGE_CALLBACK)
        interaction_id = data["id"]
        if not self.remember(f"interaction:{interaction_id}"):
            return
        action = callback_data(target, MANAGE_CALLBACK if manage else CALLBACK_PREFIX)
        group = data.get("group_openid")
        scope: Scope = "group" if group else "private"
        openid = data.get("group_member_openid" if group else "user_openid")
        valid_scene = (
            manage
            if group
            else data.get("scene") == "c2c" or data.get("chat_type") == 2
        )
        try:
            if not action or not openid or not valid_scene:
                await self.qq.acknowledge(interaction_id, code=4)
                return
            superuser = await self.database.is_superuser(openid)
            if not superuser and not (
                group
                and await self.database.group_member_role(group, openid) in ADMIN_ROLES
            ):
                await self.qq.acknowledge(interaction_id, code=4)
                return
            button_id = resolved.get("button_id") or ""
            source = self.cards.clicked(scope, group or openid, button_id)
            if source is None or manage != (source.owner is not None):
                await self.qq.acknowledge(interaction_id, code=3)
                return
            if manage and source.owner != openid:
                await self.qq.acknowledge(interaction_id, code=4)
                return
            if manage and source.callbacks.get(button_id) != target:
                await self.qq.acknowledge(interaction_id, code=3)
                return
            await self.qq.acknowledge(interaction_id)
            try:
                card = (
                    await self.management.click(action, source, superuser)
                    if manage
                    else await self.private_settings.click(action)
                )
            except UserInputError as exc:
                card = (
                    self.management.error(str(exc))
                    if manage
                    else await self.private_settings.home(str(exc))
                )
            except McAdminError:
                notice = "mc-admin 暂时不可用，请稍后再试"
                card = (
                    self.management.error(notice)
                    if manage
                    else await self.private_settings.home(notice)
                )
            await self.cards.send(
                source.scope,
                source.destination,
                card,
                event_id=event.get("id") or interaction_id,
                replace=source,
                owner=source.owner,
            )
        except (QQError, aiohttp.ClientError, TimeoutError):
            log.warning("card interaction failed")

    async def refresh_group_names(self) -> None:
        while True:
            openid = await self._group_refreshes.get()
            try:
                name = await self.qq.group_name(openid)
            except (QQError, aiohttp.ClientError, TimeoutError):
                log.warning("QQ group name unavailable")
            else:
                if name:
                    await self.database.save_group_name(openid, name)
            await asyncio.sleep(2)

    async def consume_qq(self) -> None:
        async for event in self.qq.events():
            await self.handle(event)

    async def run(self) -> None:
        await self.startup()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.consume_qq())
            tasks.create_task(consume_game(self.mc, self.database, self.relay))
            tasks.create_task(deliver_pending(self.database, self.qq, self.mc))
            tasks.create_task(watch_restarts(self.database, self.mc))
            tasks.create_task(self.refresh_group_names())
