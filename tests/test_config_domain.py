from pathlib import Path

import pytest
from pydantic import ValidationError

from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.domain import (
    UserInputError,
    format_server_list,
    online_servers,
    parse_targeted_text,
    translate_action,
    translate_whitelist,
    validate_player_name,
)
from mc_admin_qqbot.mcadmin import OnlinePlayer, ServerOverview


def server(
    server_id: str,
    port: int,
    status: str = "HEALTHY",
    players: tuple[str, ...] = (),
) -> ServerOverview:
    return ServerOverview(
        id=server_id,
        name=server_id,
        gamePort=port,
        status=status,
        online_players=[
            OnlinePlayer(name=name, uuid=name.lower(), player_db_id=index)
            for index, name in enumerate(players, 1)
        ],
    )


def test_settings_validate_urls_database_and_defaults(tmp_path: Path) -> None:
    base = {
        "qq_app_id": "test-app",
        "qq_app_secret": "secret",
        "_env_file": None,
        "mc_admin_base_url": "https://example.test/api/",
        "mc_admin_master_token": "secret",
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'db').as_posix()}",
    }
    settings = Settings(**base)
    assert settings.mc_admin_api_url == "https://example.test/api"
    assert settings.database_path == tmp_path / "db"
    with pytest.raises(ValidationError, match="must end with /api"):
        Settings(**{**base, "mc_admin_base_url": "https://example.test/root"})
    with pytest.raises(ValidationError, match="Only sqlite"):
        Settings(**{**base, "database_url": "postgresql+asyncpg://localhost/db"})
    with pytest.raises(ValidationError, match="SQLite file"):
        Settings(**{**base, "database_url": "sqlite+aiosqlite:///:memory:"})
    with pytest.raises(ValidationError, match="query parameters"):
        Settings(**{**base, "database_url": "sqlite+aiosqlite:///bot.db?mode=ro"})


def test_visible_servers_and_list_format_are_stable() -> None:
    servers = [
        server("empty", 25567),
        server("busy", 25565, players=("Alice", "Bob")),
        server("starting", 25566, "STARTING"),
        server("offline", 25564, "CREATED"),
    ]
    assert [item.id for item in online_servers(servers)] == [
        "busy",
        "starting",
        "empty",
    ]
    assert format_server_list(servers) == (
        "[busy]：Alice、Bob\n没人：[empty]\n无响应：[starting]"
    )
    assert format_server_list([server("off", 1, "CREATED")]) == ("现在没有服务器在运行")


def test_cli_settings_read_environment(monkeypatch, tmp_path):
    values = {
        "QQ_APP_ID": "environment-app",
        "QQ_APP_SECRET": "environment-secret",
        "MC_ADMIN_BASE_URL": "http://example.test/api",
        "MC_ADMIN_MASTER_TOKEN": "environment-master-token",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path.as_posix()}/env.db",
    }
    for key, value in values.items():
        monkeypatch.setenv("MC_ADMIN_QQBOT_" + key, value)
    loaded = Settings.model_validate({})
    assert loaded.qq_app_id == "environment-app"
    assert loaded.qq_app_secret.get_secret_value() == "environment-secret"


def test_target_selection_exact_prefix_default_and_safety() -> None:
    servers = [server("vanilla", 25565), server("vanish", 25566)]
    exact = parse_targeted_text("hello /vanish", servers, None, strict_selector=False)
    assert (exact.text, exact.server.id, exact.explicit) == ("hello", "vanish", True)
    prefix = parse_targeted_text("hello /van", servers, None, strict_selector=False)
    assert prefix.server.id == "vanilla"
    unmatched_chat = parse_targeted_text(
        "hello /missing", servers, None, strict_selector=False
    )
    assert unmatched_chat.text == "hello /missing"
    assert unmatched_chat.server.id == "vanilla"
    default = parse_targeted_text("hello", servers, "vanish", strict_selector=False)
    assert default.server.id == "vanish"
    selector_only = parse_targeted_text("/vanish", servers, None, strict_selector=True)
    assert selector_only.text == ""
    with pytest.raises(UserInputError, match="找不到服务器"):
        parse_targeted_text("/missing", servers, None, strict_selector=True)
    with pytest.raises(UserInputError, match="显式指定"):
        parse_targeted_text(
            "", servers, None, strict_selector=True, require_explicit=True
        )
    with pytest.raises(UserInputError, match="默认服务器"):
        parse_targeted_text("", servers, "offline", strict_selector=True)
    with pytest.raises(UserInputError, match="没有可用"):
        parse_targeted_text(
            "", [server("off", 1, "CREATED")], None, strict_selector=True
        )


@pytest.mark.parametrize("name", ["Notch", "A", "bot_123", "x" * 16])
def test_player_name_validation_accepts_safe_names(name: str) -> None:
    assert validate_player_name(name) == name


@pytest.mark.parametrize("name", ["", "x" * 17, "a b", "@a", "玩家", "a;stop"])
def test_player_name_validation_rejects_injection(name: str) -> None:
    with pytest.raises(UserInputError):
        validate_player_name(name)


def test_rcon_translation_never_exposes_unknown_output() -> None:
    assert translate_action("whitelist_add", "Notch", "Added Notch") == (
        "已将 [Notch] 加入白名单"
    )
    assert "已在白名单" in translate_action(
        "whitelist_add", "Notch", "Player is already whitelisted"
    )
    assert "不在白名单" in translate_action(
        "whitelist_remove", "Notch", "Player is not whitelisted"
    )
    assert "未能封禁" in translate_action("ban", "Notch", "Nothing changed")
    assert "未被封禁" in translate_action("unban", "Notch", "Player is not banned")
    assert "移出白名单" in translate_action(
        "whitelist_remove", "Notch", "Removed Notch"
    )
    assert translate_action("ban", "Notch", "Banned Notch") == "已封禁 [Notch]"
    assert translate_action("unban", "Notch", "Pardoned Notch") == (
        "已解除 [Notch] 的封禁"
    )
    assert translate_action("unknown", "Notch", "secret raw output") == (
        "服务器已执行命令，但返回结果无法解析"
    )


def test_whitelist_translation() -> None:
    assert translate_whitelist("There are no whitelisted players") == []
    assert translate_whitelist("There are 2 players: Alice, Bob") == [
        "Alice",
        "Bob",
    ]
    assert translate_whitelist("unstructured output") is None
    assert translate_whitelist("players: @a") is None
    assert translate_whitelist("players:") == []
    assert translate_whitelist("players: Alice,") is None
    assert translate_whitelist("There are 2 players: Alice") is None
