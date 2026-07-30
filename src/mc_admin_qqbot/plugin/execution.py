from collections.abc import Awaitable, Callable
from time import perf_counter

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger

from ..domain import UserInputError
from ..mcadmin import McAdminError
from ..services.contracts import CommandResponse
from .context import command_context
from .responses import send_response, send_text


def log_command(event: MessageEvent, action: str, started: float, result: str) -> None:
    context = command_context(event)
    logger.bind(
        actor=context.actor_qq,
        context_type=context.context_type.value,
        context_id=context.context_id,
        action=action,
        duration_ms=round((perf_counter() - started) * 1000, 2),
        result=result,
    ).info("command")


def user_facing_error(exc: Exception) -> str:
    if isinstance(exc, UserInputError):
        return str(exc)
    if isinstance(exc, McAdminError):
        return "mc-admin 暂时不可用，请稍后再试"
    logger.error("unexpected command failure type={}", type(exc).__name__)
    return "命令执行失败，请稍后再试"


async def execute_command(
    bot: Bot,
    event: MessageEvent,
    operation: Callable[[], Awaitable[CommandResponse]],
    *,
    action: str,
) -> None:
    started = perf_counter()
    try:
        await send_response(bot, event, await operation())
        log_command(event, action, started, "success")
    except PermissionError:
        log_command(event, action, started, "denied")
    except Exception as exc:
        log_command(event, action, started, f"error:{type(exc).__name__}")
        await send_text(bot, event, user_facing_error(exc))
