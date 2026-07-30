from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..services.contracts import GameCommandContext


class GameCommandHandler(Protocol):
    async def execute(
        self,
        session: AsyncSession,
        context: GameCommandContext,
        argument: str,
    ) -> str: ...


@dataclass(frozen=True)
class GameCommandResult:
    command_name: str
    response: str
