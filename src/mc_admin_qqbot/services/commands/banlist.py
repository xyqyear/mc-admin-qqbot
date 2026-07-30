from ...domain import translate_player_list
from ...mcadmin import McAdminClient
from ..contracts import ListResponse, TextResponse
from .targeting import ServerTargetResolver


class BanlistCommand:
    def __init__(
        self,
        client: McAdminClient,
        targets: ServerTargetResolver,
    ) -> None:
        self.client = client
        self.targets = targets

    async def execute(self, raw: str) -> TextResponse | ListResponse:
        server_id = await self.targets.resolve(raw.strip())
        output = await self.client.rcon(server_id, "banlist players")
        names = translate_player_list("banlist", output)
        if names is None:
            return TextResponse("服务器返回的封禁名单无法解析")
        return ListResponse(f"[{server_id}] 封禁名单", tuple(names))
