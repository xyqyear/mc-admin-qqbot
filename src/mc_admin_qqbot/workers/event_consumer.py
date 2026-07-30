import asyncio
import random

from nonebot.log import logger

from ..mcadmin import (
    ChatEvent,
    HeartbeatFrame,
    McAdminClient,
    McAdminError,
    StreamResetFrame,
)
from ..repositories import Repository
from ..services.events.processor import STREAM_NAME, EventProcessor


class EventConsumer:
    def __init__(
        self,
        client: McAdminClient,
        repository: Repository,
        processor: EventProcessor,
    ) -> None:
        self.client = client
        self.repository = repository
        self.processor = processor

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                since = await self.repository.checkpoint(STREAM_NAME)
                async for frame in self.client.events(since):
                    delay = 1.0
                    if isinstance(frame, ChatEvent):
                        await self.processor.process(frame)
                    elif isinstance(frame, StreamResetFrame):
                        if frame.reason == "invalid_cursor":
                            logger.critical(
                                "mc-admin rejected the persisted event cursor; "
                                "event consumption stopped"
                            )
                            return
                        break
                    elif isinstance(frame, HeartbeatFrame):
                        continue
            except asyncio.CancelledError:
                raise
            except McAdminError as exc:
                logger.warning("mc-admin event stream unavailable: {}", exc)
            await asyncio.sleep(delay + random.uniform(0, delay / 4))
            delay = min(30.0, delay * 2)
