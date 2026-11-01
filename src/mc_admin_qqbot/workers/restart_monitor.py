import asyncio
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from ..config import Settings
from ..mcadmin import McAdminClient, McAdminError
from ..models import (
    ContextType,
    DeliveryChannel,
    DeliveryStatus,
    OutboundDelivery,
    RestartWatch,
    utc_now,
)
from ..repositories import Repository


class RestartMonitor:
    def __init__(
        self,
        settings: Settings,
        client: McAdminClient,
        repository: Repository,
    ) -> None:
        self.settings = settings
        self.client = client
        self.repository = repository

    async def run(self) -> None:
        while True:
            for watch in await self.repository.restart_watches():
                await self._check(watch)
            await asyncio.sleep(self.settings.restart_poll_interval_seconds)

    async def _check(self, watch: RestartWatch) -> None:
        now = utc_now()
        if now >= watch.deadline:
            await self._finish(watch, f"[{watch.server_id}] 重启等待超时")
            return
        try:
            healthy = (
                await self.client.status(watch.server_id, after=watch.created_at)
                == "HEALTHY"
            )
        except McAdminError:
            await self.repository.update_restart_watch(
                watch.server_id,
                observed_unhealthy=watch.observed_unhealthy,
                healthy_streak=0,
            )
            return
        observed_unhealthy = watch.observed_unhealthy or not healthy
        healthy_streak = watch.healthy_streak + 1 if healthy else 0
        stable_without_transition = (
            now - watch.created_at >= timedelta(seconds=15) and healthy_streak >= 3
        )
        if healthy and (watch.observed_unhealthy or stable_without_transition):
            await self._finish(watch, f"[{watch.server_id}] 重启完成")
            return
        await self.repository.update_restart_watch(
            watch.server_id,
            observed_unhealthy=observed_unhealthy,
            healthy_streak=healthy_streak,
        )

    async def _finish(self, watch: RestartWatch, message: str) -> None:
        channel = (
            DeliveryChannel.QQ_GROUP
            if watch.context_type == ContextType.GROUP
            else DeliveryChannel.QQ_PRIVATE
        )
        async with self.repository.sessions.begin() as session:
            statement = insert(OutboundDelivery).values(
                dedupe_key=f"restart:{watch.server_id}:{watch.created_at.isoformat()}",
                channel=channel,
                bot_self_id=watch.bot_self_id,
                destination_id=watch.context_id,
                body=message,
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
            await session.execute(
                delete(RestartWatch).where(RestartWatch.server_id == watch.server_id)
            )
