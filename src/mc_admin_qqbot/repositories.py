from datetime import timedelta
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import (
    ContextType,
    DeliveryStatus,
    EventCheckpoint,
    MessageRoute,
    OutboundDelivery,
    PlayerBinding,
    RestartWatch,
    utc_now,
)


class BindingResult(StrEnum):
    CREATED = "created"
    PLAYER_EXISTS = "player_exists"
    QQ_EXISTS = "qq_exists"


class Repository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def binding_for_qq(self, qq_id: str) -> PlayerBinding | None:
        async with self.sessions() as session:
            return await session.get(PlayerBinding, qq_id)

    async def binding_for_player(self, player_uuid: str) -> PlayerBinding | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(PlayerBinding).where(PlayerBinding.player_uuid == player_uuid)
            )

    async def bind(self, qq_id: str, player_uuid: str) -> BindingResult:
        async with self.sessions.begin() as session:
            result = await session.execute(
                insert(PlayerBinding)
                .values(qq_id=qq_id, player_uuid=player_uuid, created_at=utc_now())
                .on_conflict_do_nothing()
            )
            if result.rowcount:
                return BindingResult.CREATED
            if await session.scalar(
                select(PlayerBinding).where(PlayerBinding.player_uuid == player_uuid)
            ):
                return BindingResult.PLAYER_EXISTS
            return BindingResult.QQ_EXISTS

    async def unbind_player(self, player_uuid: str) -> str | None:
        async with self.sessions.begin() as session:
            binding = await session.scalar(
                select(PlayerBinding).where(PlayerBinding.player_uuid == player_uuid)
            )
            if binding is None:
                return None
            qq_id = binding.qq_id
            await session.delete(binding)
            return qq_id

    async def record_route(
        self,
        *,
        bot_self_id: str,
        context_type: ContextType,
        context_id: str,
        message_id: str,
        server_id: str,
    ) -> None:
        statement = insert(MessageRoute).values(
            bot_self_id=bot_self_id,
            context_type=context_type,
            context_id=context_id,
            message_id=message_id,
            server_id=server_id,
            created_at=utc_now(),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                MessageRoute.bot_self_id,
                MessageRoute.context_type,
                MessageRoute.context_id,
                MessageRoute.message_id,
            ],
            set_={"server_id": server_id, "created_at": utc_now()},
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)

    async def route_for(
        self,
        *,
        bot_self_id: str,
        context_type: ContextType,
        context_id: str,
        message_id: str,
    ) -> str | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(MessageRoute.server_id).where(
                    MessageRoute.bot_self_id == bot_self_id,
                    MessageRoute.context_type == context_type,
                    MessageRoute.context_id == context_id,
                    MessageRoute.message_id == message_id,
                )
            )

    async def checkpoint(self, stream_name: str) -> str | None:
        async with self.sessions() as session:
            row = await session.get(EventCheckpoint, stream_name)
            return row.cursor if row else None

    async def pending_deliveries(self, limit: int = 20) -> list[OutboundDelivery]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(OutboundDelivery)
                .where(
                    OutboundDelivery.status == DeliveryStatus.PENDING,
                    OutboundDelivery.available_at <= utc_now(),
                )
                .order_by(OutboundDelivery.id)
                .limit(limit)
            )
            return list(result)

    async def mark_delivery_sent(
        self, delivery_id: int, remote_message_id: str | None
    ) -> None:
        async with self.sessions.begin() as session:
            delivery = await session.get(OutboundDelivery, delivery_id)
            if delivery is None:
                return
            delivery.status = DeliveryStatus.SENT
            delivery.sent_at = utc_now()
            delivery.remote_message_id = remote_message_id
            delivery.last_error = None
            if remote_message_id and delivery.route_server_id and delivery.bot_self_id:
                context_type = (
                    ContextType.GROUP
                    if delivery.channel.value == "qq_group"
                    else ContextType.PRIVATE
                )
                context_id = delivery.destination_id
                route = insert(MessageRoute).values(
                    bot_self_id=delivery.bot_self_id,
                    context_type=context_type,
                    context_id=context_id,
                    message_id=remote_message_id,
                    server_id=delivery.route_server_id,
                    created_at=utc_now(),
                )
                await session.execute(
                    route.on_conflict_do_update(
                        index_elements=[
                            MessageRoute.bot_self_id,
                            MessageRoute.context_type,
                            MessageRoute.context_id,
                            MessageRoute.message_id,
                        ],
                        set_={
                            "server_id": delivery.route_server_id,
                            "created_at": utc_now(),
                        },
                    )
                )

    async def mark_delivery_failed(self, delivery_id: int, error: str) -> None:
        async with self.sessions.begin() as session:
            delivery = await session.get(OutboundDelivery, delivery_id)
            if delivery is None:
                return
            delivery.attempts += 1
            delay = min(30, 2 ** min(delivery.attempts, 5))
            delivery.available_at = utc_now() + timedelta(seconds=delay)
            delivery.last_error = error[:2000]

    async def restart_watches(self) -> list[RestartWatch]:
        async with self.sessions() as session:
            return list(await session.scalars(select(RestartWatch)))

    async def add_restart_watch(self, watch: RestartWatch) -> bool:
        try:
            async with self.sessions.begin() as session:
                session.add(watch)
                await session.flush()
            return True
        except IntegrityError:
            return False

    async def update_restart_watch(
        self,
        server_id: str,
        *,
        observed_unhealthy: bool,
        healthy_streak: int,
    ) -> None:
        async with self.sessions.begin() as session:
            watch = await session.get(RestartWatch, server_id)
            if watch:
                watch.observed_unhealthy = observed_unhealthy
                watch.healthy_streak = healthy_streak
                watch.updated_at = utc_now()

    async def delete_restart_watch(self, server_id: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(RestartWatch).where(RestartWatch.server_id == server_id)
            )
