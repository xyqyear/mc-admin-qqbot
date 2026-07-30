from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.rule import Rule

from ..models import ContextType
from ..runtime import get_application
from ..services.contracts import CommandContext


async def authorized_context(event: MessageEvent) -> bool:
    application = get_application()
    if str(event.self_id) != str(application.settings.qq_bot_id):
        return False
    if isinstance(event, GroupMessageEvent):
        return event.group_id == application.settings.qq_group_id
    if isinstance(event, PrivateMessageEvent):
        return event.user_id in application.settings.superusers
    return False


async def has_reply(event: MessageEvent) -> bool:
    return event.reply is not None


def is_admin(event: MessageEvent) -> bool:
    application = get_application()
    if event.user_id in application.settings.superusers:
        return True
    return isinstance(event, GroupMessageEvent) and event.sender.role in {
        "owner",
        "admin",
    }


def command_context(event: MessageEvent) -> CommandContext:
    if isinstance(event, GroupMessageEvent):
        context_type = ContextType.GROUP
        context_id = str(event.group_id)
    else:
        context_type = ContextType.PRIVATE
        context_id = str(event.user_id)
    return CommandContext(
        bot_self_id=str(event.self_id),
        context_type=context_type,
        context_id=context_id,
        actor_qq=str(event.user_id),
        message_id=str(event.message_id),
        is_admin=is_admin(event),
    )


context_rule = Rule(authorized_context)
reply_rule = context_rule & Rule(has_reply)
