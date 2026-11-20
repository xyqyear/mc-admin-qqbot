import logging
import secrets
from dataclasses import dataclass

import aiohttp

from .models import Scope
from .qq import Card, QQClient, QQError, QQMessage, SentMessage

log = logging.getLogger(__name__)
MAX_ACTIVE_CARDS = 4096


@dataclass(frozen=True)
class SentCard:
    card_id: str
    scope: Scope
    destination: str
    message: SentMessage
    commands: tuple[list[str], ...]


class Cards:
    def __init__(self, qq: QQClient) -> None:
        self.qq = qq
        self._active: dict[str, SentCard] = {}

    def clicked(
        self, scope: Scope, destination: str, button_id: str
    ) -> SentCard | None:
        card = self._active.get(button_id.partition("-")[0])
        if card and (card.scope, card.destination) == (scope, destination):
            return card
        return None

    def submitted(self, message: QQMessage) -> SentCard | None:
        if not message.ref_msg_idx:
            return None
        command = message.content.split()
        for card in reversed(self._active.values()):
            if (card.scope, card.destination, card.message.ref_idx) == (
                message.scope,
                message.destination,
                message.ref_msg_idx,
            ) and any(
                len(command) > len(prefix) and command[: len(prefix)] == prefix
                for prefix in card.commands
            ):
                return card
        return None

    async def send(
        self,
        scope: Scope,
        destination: str,
        card: Card,
        *,
        msg_id: str | None = None,
        event_id: str | None = None,
        replace: SentCard | None = None,
    ) -> None:
        card_id = secrets.token_hex(8)
        sent = await self.qq.send_card(
            scope, destination, card, card_id=card_id, msg_id=msg_id, event_id=event_id
        )
        self._active[card_id] = SentCard(
            card_id,
            scope,
            destination,
            sent,
            tuple(
                button.data.split()
                for row in card.rows
                for button in row
                if button.command
            ),
        )
        if replace:
            self._active.pop(replace.card_id, None)
            try:
                await self.qq.recall(
                    replace.scope, replace.destination, replace.message.id
                )
            except (QQError, aiohttp.ClientError, TimeoutError):
                log.info(
                    "QQ could not recall the previous card scope=%s", replace.scope
                )
        if len(self._active) > MAX_ACTIVE_CARDS:
            self._active.pop(next(iter(self._active)))
