from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg

from ...runtime import get_application
from ..context import command_context, context_rule
from ..execution import execute_command

restart_matcher = on_command("restart", rule=context_rule, priority=2, block=True)


@restart_matcher.handle()
async def handle_restart(
    bot: Bot,
    event: MessageEvent,
    argument: Message = CommandArg(),
) -> None:
    application = get_application()
    await execute_command(
        bot,
        event,
        lambda: application.commands.restart.execute(
            command_context(event), argument.extract_plain_text()
        ),
        action="restart",
    )
