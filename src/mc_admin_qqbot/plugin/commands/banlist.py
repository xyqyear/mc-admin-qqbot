from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg

from ...runtime import get_application
from ..context import context_rule
from ..execution import execute_command

banlist_matcher = on_command("banlist", rule=context_rule, priority=2, block=True)


@banlist_matcher.handle()
async def handle_banlist(
    bot: Bot,
    event: MessageEvent,
    argument: Message = CommandArg(),
) -> None:
    application = get_application()
    await execute_command(
        bot,
        event,
        lambda: application.commands.banlist.execute(argument.extract_plain_text()),
        action="banlist",
    )
