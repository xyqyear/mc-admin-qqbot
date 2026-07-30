from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg

from ...runtime import get_application
from ..context import command_context, context_rule
from ..execution import execute_command

whitelist_matcher = on_command(
    "whitelist",
    rule=context_rule,
    priority=2,
    block=True,
)


@whitelist_matcher.handle()
async def handle_whitelist(
    bot: Bot,
    event: MessageEvent,
    argument: Message = CommandArg(),
) -> None:
    application = get_application()
    await execute_command(
        bot,
        event,
        lambda: application.commands.whitelist.execute(
            command_context(event), argument.extract_plain_text()
        ),
        action="whitelist",
    )
