from ...domain import (
    UserInputError,
    translate_action,
    translate_player_list,
    validate_player_name,
)
from ...mcadmin import McAdminClient
from ..contracts import CommandContext, ListResponse, TextResponse
from .targeting import ServerTargetResolver


class WhitelistCommand:
    def __init__(
        self,
        client: McAdminClient,
        targets: ServerTargetResolver,
    ) -> None:
        self.client = client
        self.targets = targets

    async def execute(
        self,
        context: CommandContext,
        raw: str,
    ) -> TextResponse | ListResponse:
        parts = raw.strip().split()
        if not parts or parts[0] not in {"add", "remove", "list"}:
            raise UserInputError(
                "用法：/whitelist list 或 /whitelist add/remove <玩家> [/<服务器>]"
            )
        operation = parts[0]
        if operation in {"add", "remove"} and not context.is_admin:
            raise PermissionError
        if operation == "list":
            if len(parts) > 2:
                raise UserInputError("用法：/whitelist list [/<服务器>]")
            target = await self.targets.resolve(" ".join(parts[1:]))
            output = await self.client.rcon(target, "whitelist list")
            names = translate_player_list("whitelist", output)
            if names is None:
                return TextResponse("服务器返回的白名单无法解析")
            return ListResponse(f"[{target}] 白名单", tuple(names))
        if len(parts) not in {2, 3}:
            raise UserInputError("用法：/whitelist add/remove <玩家> [/<服务器>]")
        player = validate_player_name(parts[1])
        server_id = await self.targets.resolve(" ".join(parts[2:]))
        output = await self.client.rcon(
            server_id, f"whitelist {operation} {player}"
        )
        return TextResponse(
            translate_action(f"whitelist_{operation}", player, output)
        )
