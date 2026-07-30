from ...domain import UserInputError, translate_action, validate_player_name
from ...mcadmin import McAdminClient
from ..contracts import CommandContext, TextResponse
from .targeting import ServerTargetResolver


class UnbanCommand:
    def __init__(
        self,
        client: McAdminClient,
        targets: ServerTargetResolver,
    ) -> None:
        self.client = client
        self.targets = targets

    async def execute(self, context: CommandContext, raw: str) -> TextResponse:
        if not context.is_admin:
            raise PermissionError
        parts = raw.strip().split()
        if len(parts) not in {1, 2}:
            raise UserInputError("用法：/unban <玩家> [/<服务器>]")
        player = validate_player_name(parts[0])
        server_id = await self.targets.resolve(" ".join(parts[1:]))
        output = await self.client.rcon(server_id, f"pardon {player}")
        return TextResponse(translate_action("unban", player, output))
