from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)

from ..services.contracts import (
    CommandResponse,
    ListResponse,
    SilentResponse,
    TextResponse,
)


async def send_text(bot: Bot, event: MessageEvent, text: str) -> None:
    await bot.send(event, Message(MessageSegment.text(text)))


async def send_response(
    bot: Bot,
    event: MessageEvent,
    response: CommandResponse,
) -> None:
    if isinstance(response, SilentResponse):
        return
    if isinstance(response, TextResponse):
        await send_text(bot, event, response.text)
        return
    await send_list(bot, event, response)


async def send_list(bot: Bot, event: MessageEvent, response: ListResponse) -> None:
    if not response.items:
        await send_text(bot, event, f"{response.title}\n暂无")
        return
    text = f"{response.title}\n" + "、".join(response.items)
    if not isinstance(event, GroupMessageEvent) or (
        len(response.items) <= 20 and len(text) <= 800
    ):
        await send_text(bot, event, text)
        return
    nodes = [
        {
            "type": "node",
            "data": {
                "user_id": str(event.self_id),
                "nickname": response.title,
                "content": Message(MessageSegment.text(item)),
            },
        }
        for item in response.items
    ]
    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=nodes,
        )
    except Exception:
        await send_text(bot, event, "名单较长，但当前 QQ 连接器不支持合并转发")
