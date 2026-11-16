import re
import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Database
from .domain import UserInputError
from .mcadmin import ChatEvent
from .models import BindRequest, QQIdentity, User, utc_now

CODE_RE = re.compile(r"^[0-9]{4}$")
BIND_HELP = "绑定命令：bind、bind get、bind remove、bind help"


class Bindings:
    def __init__(self, database: Database, bot_name: str) -> None:
        self.database = database
        self.bot_name = bot_name

    async def game(self, session: AsyncSession, event: ChatEvent, argument: str) -> str:
        uuid = event.player.uuid
        if uuid is None:
            return "目前无法确认你的玩家身份，请稍后再试"
        user = await session.scalar(select(User).where(User.player_uuid == uuid))
        if argument == "get":
            if user is None:
                return "当前没有绑定机器人账号"
            name = await session.scalar(
                select(QQIdentity.username).where(
                    QQIdentity.user_id == user.id,
                    QQIdentity.app_id == self.database.app_id,
                )
            )
            return f"当前绑定机器人账号：{name or 'QQ用户'}"
        if argument == "remove":
            cancelled = await session.scalar(
                delete(BindRequest)
                .where(BindRequest.player_uuid == uuid)
                .returning(BindRequest.code)
            )
            if user:
                user.player_uuid = None
                return "已解除机器人账号绑定"
            return "已取消待确认绑定" if cancelled else "当前没有绑定机器人账号"
        if argument:
            return BIND_HELP
        if user:
            return "这个玩家已经绑定，请先使用 bind remove 解绑"
        now = utc_now()
        await session.execute(
            delete(BindRequest).where(
                (BindRequest.expires_at <= now) | (BindRequest.player_uuid == uuid)
            )
        )
        code = str(secrets.randbelow(9000) + 1000)
        while await session.get(BindRequest, code) is not None:
            code = str(secrets.randbelow(9000) + 1000)
        session.add(
            BindRequest(
                code=code,
                player_uuid=uuid,
                player_name=event.player.name,
                server_id=event.server_id,
                expires_at=now + timedelta(minutes=5),
            )
        )
        return (
            f"绑定验证码：{code}（5分钟内有效）。"
            f"请私聊机器人“{self.bot_name}”发送这4位数字完成绑定。"
        )

    async def confirm(self, user_id: int, code: str) -> str:
        async with self.database.sessions.begin() as session:
            request = (
                await session.execute(
                    delete(BindRequest)
                    .where(BindRequest.code == code, BindRequest.expires_at > utc_now())
                    .returning(BindRequest)
                )
            ).scalar_one_or_none()
            if request is None:
                return "验证码无效或已过期，请在游戏内重新输入 \\\\bind"
            user = await session.get(User, user_id)
            assert user is not None
            if user.player_uuid is not None:
                raise UserInputError("你已经绑定了玩家，请先在游戏内解绑")
            owner = await session.scalar(
                select(User).where(User.player_uuid == request.player_uuid)
            )
            if owner is not None:
                raise UserInputError("这个玩家已经绑定了其他机器人账号")
            user.player_uuid = request.player_uuid
            return f"绑定成功：{request.player_name}"
