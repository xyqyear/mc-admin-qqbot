from time import perf_counter

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from ..runtime import get_application
from .context import command_context, reply_rule
from .execution import log_command, user_facing_error
from .responses import send_text

reply_matcher = on_message(rule=reply_rule, priority=1, block=True)


@reply_matcher.handle()
async def handle_reply(bot: Bot, event: MessageEvent) -> None:
    if event.reply is None:
        return
    application = get_application()
    context = command_context(event)
    server_id = await application.repository.route_for(
        bot_self_id=context.bot_self_id,
        context_type=context.context_type,
        context_id=context.context_id,
        message_id=str(event.reply.message_id),
    )
    if server_id is None:
        return
    text = event.get_message().extract_plain_text().strip()
    started = perf_counter()
    try:
        await application.commands.say.execute(
            context,
            text,
            routed_server=server_id,
        )
        log_command(event, "reply", started, "success")
    except Exception as exc:
        log_command(event, "reply", started, f"error:{type(exc).__name__}")
        await send_text(bot, event, user_facing_error(exc))
