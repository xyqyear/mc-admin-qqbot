from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain import QQ_ID_RE
from ...models import PlayerBinding
from ..contracts import GameCommandContext

BIND_HELP = "绑定命令：bind <QQ号>、bind get、bind remove、bind help"


class BindCommand:
    async def execute(
        self,
        session: AsyncSession,
        context: GameCommandContext,
        argument: str,
    ) -> str:
        if context.player_uuid is None:
            return "目前无法确认你的玩家身份，请稍后再试"
        parts = argument.split()
        if not parts or parts[0] == "help":
            return BIND_HELP
        player_binding = await session.scalar(
            select(PlayerBinding).where(
                PlayerBinding.player_uuid == context.player_uuid
            )
        )
        if parts[0] == "get" and len(parts) == 1:
            return (
                f"当前绑定 QQ：{player_binding.qq_id}"
                if player_binding
                else "当前没有绑定 QQ"
            )
        if parts[0] == "remove" and len(parts) == 1:
            if player_binding is None:
                return "当前没有绑定 QQ"
            qq_id = player_binding.qq_id
            await session.delete(player_binding)
            return f"已解除 QQ {qq_id} 的绑定"
        if len(parts) != 1 or not QQ_ID_RE.fullmatch(parts[0]):
            return "QQ 号必须是 5 至 12 位数字"
        if player_binding:
            return "这个玩家已经绑定了 QQ"
        qq_id = parts[0]
        if await session.get(PlayerBinding, qq_id):
            return "这个 QQ 已绑定其他玩家"
        session.add(PlayerBinding(qq_id=qq_id, player_uuid=context.player_uuid))
        await session.flush()
        return f"绑定成功：QQ {qq_id}"
