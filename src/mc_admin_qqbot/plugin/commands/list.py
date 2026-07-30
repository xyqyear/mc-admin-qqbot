from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from ...runtime import get_application
from ..context import context_rule
from ..execution import execute_command

list_matcher = on_command(
    "list",
    aliases={"ping", "l"},
    rule=context_rule,
    priority=2,
    block=True,
)


@list_matcher.handle()
async def handle_list(bot: Bot, event: MessageEvent) -> None:
    await execute_command(
        bot,
        event,
        get_application().commands.list_servers.execute,
        action="list",
    )
