from ...config import Settings
from ...domain import UserInputError, parse_targeted_text
from ...mcadmin import McAdminClient


class ServerTargetResolver:
    def __init__(self, settings: Settings, client: McAdminClient) -> None:
        self.settings = settings
        self.client = client

    async def resolve(
        self,
        raw: str,
        *,
        require_explicit: bool = False,
    ) -> str:
        target = parse_targeted_text(
            raw,
            await self.client.overview(),
            self.settings.excluded_servers,
            self.settings.default_server,
            strict_selector=True,
            require_explicit=require_explicit,
        )
        if target.text:
            raise UserInputError("服务器选择器必须是末尾的 /<服务器>")
        return target.server.id
