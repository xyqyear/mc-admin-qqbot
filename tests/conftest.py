from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebug import NONEBOT_INIT_KWARGS, NONEBOT_START_LIFESPAN

from mc_admin_qqbot.application import Application
from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.database import Database
from mc_admin_qqbot.runtime import set_application


def pytest_configure(config: pytest.Config) -> None:
    config.stash[NONEBOT_INIT_KWARGS] = {
        "driver": "~fastapi+~httpx+~websockets",
        "command_start": {"/"},
    }
    config.stash[NONEBOT_START_LIFESPAN] = False


def make_settings(database_url: str, **overrides) -> Settings:
    values = {
        "qq_bot_id": 10000,
        "qq_group_id": 20000,
        "onebot_access_token": "onebot-test-token",
        "mc_admin_base_url": "http://mc-admin.test/api",
        "mc_admin_master_token": "mc-admin-test-token",
        "superusers": {30000},
        "excluded_servers": {"hidden"},
        "database_url": database_url,
        "restart_poll_interval_seconds": 0.01,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init() -> AsyncIterator[None]:
    temporary = TemporaryDirectory()
    database_url = f"sqlite+aiosqlite:///{Path(temporary.name, 'plugin.db').as_posix()}"
    settings = make_settings(database_url)
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)
    application = Application(settings, driver)
    set_application(application)
    nonebot.load_plugin("mc_admin_qqbot.plugin")
    yield
    await application.database.dispose()
    temporary.cleanup()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}")


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.database_url)
    await database.migrate()
    yield database
    await database.dispose()
