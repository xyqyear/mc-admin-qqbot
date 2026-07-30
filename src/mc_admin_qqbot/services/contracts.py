from dataclasses import dataclass

from ..models import ContextType


@dataclass(frozen=True)
class CommandContext:
    bot_self_id: str
    context_type: ContextType
    context_id: str
    actor_qq: str
    message_id: str
    is_admin: bool


@dataclass(frozen=True)
class GameCommandContext:
    server_id: str
    player_name: str
    player_uuid: str | None


@dataclass(frozen=True)
class TextResponse:
    text: str


@dataclass(frozen=True)
class ListResponse:
    title: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class SilentResponse:
    pass


type CommandResponse = TextResponse | ListResponse | SilentResponse
