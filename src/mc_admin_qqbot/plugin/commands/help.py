from time import perf_counter

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from ...domain import HELP_TEXT
from ..context import context_rule
from ..execution import log_command
from ..responses import send_text

help_matcher = on_command("help", rule=context_rule, priority=2, block=True)


@help_matcher.handle()
async def handle_help(bot: Bot, event: MessageEvent) -> None:
    started = perf_counter()
    await send_text(bot, event, HELP_TEXT)
    log_command(event, "help", started, "success")
