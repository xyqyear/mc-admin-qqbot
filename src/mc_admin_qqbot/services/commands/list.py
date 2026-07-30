from ...config import Settings
from ...domain import format_server_list
from ...mcadmin import McAdminClient
from ..contracts import TextResponse


class ListCommand:
    def __init__(self, settings: Settings, client: McAdminClient) -> None:
        self.settings = settings
        self.client = client

    async def execute(self) -> TextResponse:
        return TextResponse(
            format_server_list(
                await self.client.overview(), self.settings.excluded_servers
            )
        )
