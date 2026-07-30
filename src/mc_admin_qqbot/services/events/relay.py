from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...mcadmin import ChatEvent
from ...models import DeliveryChannel, DeliveryStatus, OutboundDelivery, utc_now


class GameRelayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def enqueue(
        self,
        session: AsyncSession,
        event: ChatEvent,
        body: str,
    ) -> None:
        statement = insert(OutboundDelivery).values(
            dedupe_key=f"mc-chat:{event.cursor}:qq-relay",
            channel=DeliveryChannel.QQ_GROUP,
            bot_self_id=str(self.settings.qq_bot_id),
            destination_id=str(self.settings.qq_group_id),
            server_id=event.server_id,
            body=f"[{event.server_id}] <{event.player.name}>：{body}",
            route_server_id=event.server_id,
            status=DeliveryStatus.PENDING,
            attempts=0,
            available_at=utc_now(),
            created_at=utc_now(),
        )
        await session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[OutboundDelivery.dedupe_key]
            )
        )
