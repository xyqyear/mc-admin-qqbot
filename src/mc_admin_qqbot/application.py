import asyncio
import logging
import secrets
import time

from .binding import CODE_RE, Bindings
from .commands import Commands
from .config import Settings
from .database import Database
from .domain import UserInputError
from .mcadmin import McAdminClient, McAdminError
from .models import User
from .qq import QQClient, QQMessage
from .relay import GameRelay
from .workers import consume_game, deliver_pending, watch_restarts

log = logging.getLogger(__name__)


class Application:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        qq: QQClient,
        mc: McAdminClient,
    ) -> None:
        self.settings = settings
        self.database = database
        self.qq = qq
        self.mc = mc
        self.bindings = Bindings(database, settings.qq_bot_name)
        self.commands = Commands(settings, database, mc)
        self.relay = GameRelay(settings, database, self.bindings)
        self.claim_token: str | None = None
        self._claim_lock = asyncio.Lock()
        self._seen: dict[str, None] = {}
        self._attempts: dict[int, tuple[float, int]] = {}

    async def startup(self) -> None:
        await self.database.initialize()
        self.claim_token = secrets.token_urlsafe(24)
        print(
            f"超级管理员绑定口令：{self.claim_token}\n"
            "请私聊机器人发送此口令。本次启动仅可使用一次，已有管理员权限保留。",
            flush=True,
        )

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

    async def handle(self, event: dict) -> None:
        message = QQMessage.from_event(event)
        if message.author.get("bot") or (
            message.scope == "group"
            and message.destination != self.settings.qq_group_openid
        ):
            return
        if message.id in self._seen:
            return
        user = await self.database.observe(message)
        self._seen[message.id] = None
        if len(self._seen) > 4096:
            self._seen.pop(next(iter(self._seen)))
        try:
            if message.scope == "private":
                if await self.claim_admin(message, user):
                    response = "已绑定为超级管理员，权限已保存。"
                elif CODE_RE.fullmatch(message.content):
                    if self.allow_code_attempt(user.id):
                        response = await self.bindings.confirm(user.id, message.content)
                        log.info("binding confirmation user=%s", user.id)
                    else:
                        response = "验证码尝试过于频繁，请一分钟后再试"
                elif user.is_superuser:
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
        except Exception as exc:
            log.error("QQ command failed type=%s", type(exc).__name__)
            response = "命令执行失败，请稍后再试"
        if response:
            await self.database.reply(message, response)

    async def consume_qq(self) -> None:
        async for event in self.qq.events():
            await self.handle(event)

    async def run(self) -> None:
        await self.startup()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.consume_qq())
            tasks.create_task(consume_game(self.mc, self.database, self.relay))
            tasks.create_task(deliver_pending(self.database, self.qq, self.mc))
            tasks.create_task(watch_restarts(self.settings, self.database, self.mc))
