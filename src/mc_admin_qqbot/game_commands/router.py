from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from ..mcadmin import ChatEvent
from ..services.contracts import GameCommandContext
from .context import GameCommandHandler, GameCommandResult


class GameCommandRouter:
    def __init__(self, commands: Mapping[str, GameCommandHandler]) -> None:
        self.commands = commands

    async def dispatch(
        self,
        session: AsyncSession,
        event: ChatEvent,
        body: str,
    ) -> GameCommandResult | None:
        command = body.strip()
        name, separator, argument = command.partition(" ")
        handler = self.commands.get(name)
        if handler is None:
            return None
        response = await handler.execute(
            session,
            GameCommandContext(
                server_id=event.server_id,
                player_name=event.player.name,
                player_uuid=event.player.uuid,
            ),
            argument if separator else "",
        )
        return GameCommandResult(command_name=name, response=response)
