import re
from dataclasses import dataclass

from .mcadmin import ServerOverview

ONLINE_STATUSES = {"RUNNING", "STARTING", "HEALTHY"}
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
BANLIST_HEADER_RE = re.compile(r"^There are (\d+) ban\(s\):$", re.IGNORECASE)


class UserInputError(ValueError):
    pass


@dataclass(frozen=True)
class TargetedText:
    text: str
    server: ServerOverview
    explicit: bool


def visible_online_servers(
    servers: list[ServerOverview], excluded: set[str]
) -> list[ServerOverview]:
    return sorted(
        (
            server
            for server in servers
            if server.id not in excluded and server.status in ONLINE_STATUSES
        ),
        key=lambda server: (server.game_port, server.id),
    )


def resolve_selector(
    servers: list[ServerOverview], selector: str
) -> ServerOverview | None:
    for server in servers:
        if server.id == selector:
            return server
    return next((server for server in servers if server.id.startswith(selector)), None)


def parse_targeted_text(
    raw: str,
    servers: list[ServerOverview],
    excluded: set[str],
    default_server: str | None,
    *,
    strict_selector: bool,
    require_explicit: bool = False,
) -> TargetedText:
    available = visible_online_servers(servers, excluded)
    if not available:
        raise UserInputError("当前没有可用的服务器")

    text = raw.strip()
    parts = text.rsplit(maxsplit=1)
    token = parts[-1] if parts else ""
    if token.startswith("/") and len(token) > 1:
        server = resolve_selector(available, token[1:])
        if server is not None:
            body = parts[0] if len(parts) == 2 else ""
            return TargetedText(body, server, True)
        if strict_selector:
            raise UserInputError(f"找不到服务器：{token[1:]}")

    if require_explicit:
        raise UserInputError("请显式指定要重启的服务器，例如 /restart /vanilla")

    if default_server is not None:
        server = next(
            (candidate for candidate in available if candidate.id == default_server),
            None,
        )
        if server is None:
            raise UserInputError(f"默认服务器 [{default_server}] 当前不可用")
    else:
        server = available[0]
    return TargetedText(text, server, False)


def format_server_list(servers: list[ServerOverview], excluded: set[str]) -> str:
    available = visible_online_servers(servers, excluded)
    if not available:
        return "现在没有服务器在运行"
    populated: list[str] = []
    empty: list[str] = []
    unresponsive: list[str] = []
    for server in available:
        if server.status != "HEALTHY":
            unresponsive.append(f"[{server.id}]")
        elif server.online_players:
            names = "、".join(player.name for player in server.online_players)
            populated.append(f"[{server.id}]：{names}")
        else:
            empty.append(f"[{server.id}]")
    lines = populated
    if empty:
        lines.append(f"没人：{'、'.join(empty)}")
    if unresponsive:
        lines.append(f"无响应：{'、'.join(unresponsive)}")
    return "\n".join(lines)


def validate_player_name(player: str) -> str:
    if not PLAYER_NAME_RE.fullmatch(player):
        raise UserInputError("玩家名只能包含字母、数字和下划线，且不能超过 16 个字符")
    return player


def translate_action(action: str, player: str, output: str) -> str:
    normalized = output.casefold()
    if action == "whitelist_add":
        if "already" in normalized:
            return f"[{player}] 已在白名单中"
        return f"已将 [{player}] 加入白名单"
    if action == "whitelist_remove":
        if "not whitelisted" in normalized or "nothing changed" in normalized:
            return f"[{player}] 不在白名单中"
        return f"已将 [{player}] 移出白名单"
    if action == "ban":
        if "nothing changed" in normalized or "failed" in normalized:
            return f"未能封禁 [{player}]"
        return f"已封禁 [{player}]"
    if action == "unban":
        if "not banned" in normalized or "nothing changed" in normalized:
            return f"[{player}] 未被封禁"
        return f"已解除 [{player}] 的封禁"
    return "服务器已执行命令，但返回结果无法解析"


def translate_player_list(kind: str, output: str) -> list[str] | None:
    if kind == "banlist":
        return _translate_banlist(output)
    if kind != "whitelist":
        return None

    normalized = output.casefold()
    if "there are no" in normalized or output.strip().endswith(":"):
        return []
    if ":" not in output:
        return None
    names = [name.strip() for name in output.split(":", 1)[1].split(",")]
    if not names or any(not PLAYER_NAME_RE.fullmatch(name) for name in names):
        return None
    return names


def _translate_banlist(output: str) -> list[str] | None:
    stripped = output.strip()
    if not stripped:
        return None

    lines = [line.strip() for line in stripped.splitlines()]
    if len(lines) == 1 and "there are no" in lines[0].casefold():
        return []

    header = BANLIST_HEADER_RE.fullmatch(lines[0])
    if header is None or int(header.group(1)) != len(lines) - 1:
        return None

    names: list[str] = []
    for entry in lines[1:]:
        player, separator, details = entry.partition(" was banned by ")
        source, detail_separator, reason = details.partition(": ")
        if (
            not separator
            or not source
            or not detail_separator
            or not reason
            or not PLAYER_NAME_RE.fullmatch(player)
        ):
            return None
        names.append(player)
    return names


HELP_TEXT = """可用命令
/list（/ping、/l）查看服务器和玩家
/say <消息> [/<服务器>]（/s）发送游戏消息，需要绑定
/whitelist list [页码] [/<服务器>] 查看白名单
/banlist [页码] [/<服务器>] 查看封禁列表
服务器可使用完整 ID 或前缀指定。"""

ADMIN_HELP_TEXT = """管理指令
/whitelist add/remove <玩家> [/<服务器>] 管理白名单
/ban <玩家> [/<服务器>] 封禁玩家
/unban <玩家> [/<服务器>] 解除封禁
/restart /<服务器> 重启服务器"""
