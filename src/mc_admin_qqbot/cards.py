import json
import logging
import re
import secrets
from dataclasses import dataclass

import aiohttp

from .models import Scope
from .qq import Button, Card, QQClient, QQError, QQMessage, SentMessage

log = logging.getLogger(__name__)
MAX_ACTIVE_CARDS = 4096


def callback_data(data: str, prefix: str) -> list[str]:
    try:
        parts = json.loads(data.removeprefix(prefix))
    except ValueError:
        return []
    return (
        parts
        if isinstance(parts, list) and all(isinstance(p, str) for p in parts)
        else []
    )


def callback_button(
    prefix: str, label: str, *target: str, primary: bool = False
) -> Button:
    data = prefix + json.dumps(target, ensure_ascii=False, separators=(",", ":"))
    return Button(label, data, primary)


def markdown_text(text: str) -> str:
    return re.sub(r"([\\\x60*_{}\[\]<>()#+!|])", r"\\\1", " ".join(text.split()))


def render_card(
    title: str,
    rows: list[list[Button]],
    *lines: str,
    path: tuple[str, ...] = (),
    notice: str = "",
) -> Card:
    return Card(
        "\n\n".join(
            part for part in (" › ".join(path), f"**{title}**", notice, *lines) if part
        ),
        rows,
    )


def paged_buttons[T](
    items: list[T], requested: str, prefix: str, *target: str, size: int = 4
) -> tuple[list[T], str, list[Button]]:
    count = max(1, (len(items) + size - 1) // size)
    page = min(max(0, int(requested)), count - 1)
    navigation = []
    if page:
        navigation.append(callback_button(prefix, "上一页", *target, str(page - 1)))
    if page + 1 < count:
        navigation.append(callback_button(prefix, "下一页", *target, str(page + 1)))
    return items[page * size : (page + 1) * size], f"{page + 1}/{count}", navigation


@dataclass(frozen=True)
class SentCard:
    card_id: str
    scope: Scope
    destination: str
    message: SentMessage
    commands: tuple[list[str], ...]
    owner: str | None
    callbacks: dict[str, str]


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

    def forget(self, card: SentCard) -> None:
        self._active.pop(card.card_id, None)

    async def send(
        self,
        scope: Scope,
        destination: str,
        card: Card,
        *,
        msg_id: str | None = None,
        event_id: str | None = None,
        replace: SentCard | None = None,
        owner: str | None = None,
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
            owner,
            {
                f"{card_id}-{row_index}-{button_index}": button.data
                for row_index, row in enumerate(card.rows)
                for button_index, button in enumerate(row)
                if not button.command
            },
        )
        if replace:
            self.forget(replace)
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
