import logging

from .binding import Bindings
from .config import Settings
from .database import Database
from .mcadmin import ChatEvent
from .models import EventCheckpoint

log = logging.getLogger(__name__)
GAME_PREFIXES = ("\\\\", "、、")


class GameRelay:
    def __init__(
        self, settings: Settings, database: Database, bindings: Bindings
    ) -> None:
        self.settings = settings
        self.database = database
        self.bindings = bindings

    async def process(self, event: ChatEvent) -> None:
        async with self.database.sessions.begin() as session:
            checkpoint = await session.get(EventCheckpoint, "mc-admin-chat")
            if checkpoint and int(event.cursor) <= int(checkpoint.cursor):
                return
            prefix = next(
                (p for p in GAME_PREFIXES if event.message.startswith(p)), None
            )
            if event.server_id not in self.settings.excluded_servers and prefix:
                body = event.message[len(prefix) :]
                name, _, argument = body.strip().partition(" ")
                if name == "bind":
                    response = await self.bindings.game(
                        session, event, argument.strip()
                    )
                    await self.database.enqueue(
                        session,
                        key=f"game:{event.cursor}:bind",
                        channel="game",
                        destination=event.player.name,
                        body=response,
                        server_id=event.server_id,
                    )
                    log.info("game command player=%s action=bind", event.player.uuid)
                elif body:
                    await self.database.enqueue(
                        session,
                        key=f"game:{event.cursor}:relay",
                        channel="group",
                        destination=self.settings.qq_group_openid,
                        body=f"[{event.server_id}] <{event.player.name}>：{body}",
                        server_id=event.server_id,
                    )
            await self.database.advance(session, event.cursor)
