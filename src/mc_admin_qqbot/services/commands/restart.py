from datetime import timedelta

from nonebot.log import logger

from ...config import Settings
from ...domain import UserInputError
from ...mcadmin import McAdminClient
from ...models import RestartWatch, utc_now
from ...repositories import Repository
from ..contracts import CommandContext, TextResponse
from .targeting import ServerTargetResolver


class RestartCommand:
    def __init__(
        self,
        settings: Settings,
        client: McAdminClient,
        repository: Repository,
        targets: ServerTargetResolver,
    ) -> None:
        self.settings = settings
        self.client = client
        self.repository = repository
        self.targets = targets

    async def execute(self, context: CommandContext, raw: str) -> TextResponse:
        if not context.is_admin:
            raise PermissionError
        server_id = await self.targets.resolve(raw.strip(), require_explicit=True)
        watch = RestartWatch(
            server_id=server_id,
            requested_by_qq=context.actor_qq,
            bot_self_id=context.bot_self_id,
            context_type=context.context_type,
            context_id=context.context_id,
            deadline=utc_now()
            + timedelta(seconds=self.settings.restart_timeout_seconds),
            observed_unhealthy=False,
            healthy_streak=0,
        )
        if not await self.repository.add_restart_watch(watch):
            raise UserInputError(f"[{server_id}] 已在重启监控中")
        try:
            await self.client.restart(server_id)
        except Exception:
            await self.repository.delete_restart_watch(server_id)
            raise
        logger.info(
            "command actor={} action=restart server={} result=accepted",
            context.actor_qq,
            server_id,
        )
        return TextResponse(f"[{server_id}] 已开始重启")
