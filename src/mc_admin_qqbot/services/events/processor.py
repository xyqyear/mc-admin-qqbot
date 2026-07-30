from sqlalchemy.dialects.sqlite import insert

from ...config import Settings
from ...game_commands.router import GameCommandRouter
from ...mcadmin import ChatEvent
from ...models import (
    DeliveryChannel,
    DeliveryStatus,
    EventCheckpoint,
    OutboundDelivery,
    utc_now,
)
from ...repositories import Repository
from .relay import GameRelayService

STREAM_NAME = "mc-admin-chat"
GAME_MESSAGE_PREFIXES = ("\\\\", "、、")


class EventProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        commands: GameCommandRouter,
        relay: GameRelayService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.commands = commands
        self.relay = relay

    @staticmethod
    def _checkpoint_statement(cursor: str):
        statement = insert(EventCheckpoint).values(
            stream_name=STREAM_NAME,
            cursor=cursor,
            updated_at=utc_now(),
        )
        return statement.on_conflict_do_update(
            index_elements=[EventCheckpoint.stream_name],
            set_={"cursor": cursor, "updated_at": utc_now()},
        )

    @staticmethod
    def _delivery_statement(**values):
        statement = insert(OutboundDelivery).values(**values)
        return statement.on_conflict_do_nothing(
            index_elements=[OutboundDelivery.dedupe_key]
        )

    async def process(self, event: ChatEvent) -> None:
        async with self.repository.sessions.begin() as session:
            checkpoint = await session.get(EventCheckpoint, STREAM_NAME)
            if checkpoint and checkpoint.cursor == event.cursor:
                return
            if event.server_id in self.settings.excluded_servers:
                await session.execute(self._checkpoint_statement(event.cursor))
                return
            prefix = next(
                (
                    prefix
                    for prefix in GAME_MESSAGE_PREFIXES
                    if event.message.startswith(prefix)
                ),
                None,
            )
            if prefix is None:
                await session.execute(self._checkpoint_statement(event.cursor))
                return

            body = event.message[len(prefix) :]
            result = await self.commands.dispatch(session, event, body)
            if result is not None:
                await session.execute(
                    self._delivery_statement(
                        dedupe_key=(
                            f"mc-chat:{event.cursor}:{result.command_name}-response"
                        ),
                        channel=DeliveryChannel.MC_PRIVATE,
                        destination_id=event.player.name,
                        target_player=event.player.name,
                        server_id=event.server_id,
                        body=result.response,
                        status=DeliveryStatus.PENDING,
                        attempts=0,
                        available_at=utc_now(),
                        created_at=utc_now(),
                    )
                )
            elif body:
                await self.relay.enqueue(session, event, body)
            await session.execute(self._checkpoint_statement(event.cursor))
