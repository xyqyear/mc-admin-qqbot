from nonebot.log import logger

from ...config import Settings
from ...domain import UserInputError, parse_targeted_text
from ...mcadmin import McAdminClient
from ...repositories import Repository
from ..contracts import CommandContext, SilentResponse


class SayCommand:
    def __init__(
        self,
        settings: Settings,
        client: McAdminClient,
        repository: Repository,
    ) -> None:
        self.settings = settings
        self.client = client
        self.repository = repository

    async def execute(
        self,
        context: CommandContext,
        raw: str,
        *,
        routed_server: str | None = None,
    ) -> SilentResponse:
        text = raw.strip()
        if not text:
            raise UserInputError("用法：/say <消息> [/<服务器>]")
        binding = await self.repository.binding_for_qq(context.actor_qq)
        if binding is None:
            raise UserInputError("你还没有绑定，请在游戏内输入 \\\\bind <QQ号>")
        player = await self.client.player(binding.player_uuid)
        if routed_server is None:
            target = parse_targeted_text(
                text,
                await self.client.overview(),
                self.settings.excluded_servers,
                self.settings.default_server,
                strict_selector=False,
            )
            text = target.text
            server_id = target.server.id
        else:
            server_id = routed_server
        if not text:
            raise UserInputError("消息不能为空")
        await self.client.send_message(
            server_id,
            f"*<{player.current_name}> {text}",
            color="yellow",
        )
        await self.repository.record_route(
            bot_self_id=context.bot_self_id,
            context_type=context.context_type,
            context_id=context.context_id,
            message_id=context.message_id,
            server_id=server_id,
        )
        logger.info(
            "command actor={} action=say server={} result=success",
            context.actor_qq,
            server_id,
        )
        return SilentResponse()
