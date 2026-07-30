import asyncio

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

from ..mcadmin import McAdminClient
from ..models import DeliveryChannel, OutboundDelivery
from ..repositories import Repository


class DeliveryWorker:
    def __init__(
        self,
        client: McAdminClient,
        repository: Repository,
        qq_bot_id: int,
    ) -> None:
        self.client = client
        self.repository = repository
        self.qq_bot_id = str(qq_bot_id)

    async def run(self) -> None:
        while True:
            deliveries = await self.repository.pending_deliveries()
            if not deliveries:
                await asyncio.sleep(0.5)
                continue
            for delivery in deliveries:
                try:
                    message_id = await self._deliver(delivery)
                    await self.repository.mark_delivery_sent(delivery.id, message_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "delivery id={} channel={} failed: {}",
                        delivery.id,
                        delivery.channel.value,
                        exc,
                    )
                    await self.repository.mark_delivery_failed(delivery.id, str(exc))

    async def _deliver(self, delivery: OutboundDelivery) -> str | None:
        if delivery.channel == DeliveryChannel.MC_PRIVATE:
            if not delivery.server_id or not delivery.target_player:
                raise RuntimeError("Invalid Minecraft private delivery")
            await self.client.send_message(
                delivery.server_id,
                delivery.body,
                target_player=delivery.target_player,
                color="yellow",
            )
            return None
        bot = nonebot.get_bot(self.qq_bot_id)
        message = Message(MessageSegment.text(delivery.body))
        if delivery.channel == DeliveryChannel.QQ_GROUP:
            result = await bot.call_api(
                "send_group_msg",
                group_id=int(delivery.destination_id),
                message=message,
                auto_escape=False,
            )
        else:
            result = await bot.call_api(
                "send_private_msg",
                user_id=int(delivery.destination_id),
                message=message,
                auto_escape=False,
            )
        if not isinstance(result, dict) or "message_id" not in result:
            raise RuntimeError("OneBot did not return a message ID")
        return str(result["message_id"])
