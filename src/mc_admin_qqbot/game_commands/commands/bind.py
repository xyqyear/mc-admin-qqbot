from sqlalchemy.ext.asyncio import AsyncSession

from ...services.contracts import GameCommandContext
from ...services.game_commands.bind import BindCommand


class BindGameCommand:
    def __init__(self, service: BindCommand) -> None:
        self.service = service

    async def execute(
        self,
        session: AsyncSession,
        context: GameCommandContext,
        argument: str,
    ) -> str:
        return await self.service.execute(session, context, argument)
