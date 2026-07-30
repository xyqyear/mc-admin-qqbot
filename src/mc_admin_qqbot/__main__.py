from pathlib import Path
from typing import Any, cast

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from .application import Application
from .config import get_settings
from .runtime import set_application


def main() -> None:
    settings = get_settings()
    nonebot.init(
        _env_file=Path(".env.nonebot"),
        driver="~fastapi+~httpx+~websockets",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        superusers={str(user_id) for user_id in settings.superusers},
        command_start={"/"},
    )
    driver = nonebot.get_driver()
    cast(
        Any, driver.config
    ).onebot_access_token = settings.onebot_access_token.get_secret_value()
    driver.register_adapter(OneBotV11Adapter)
    application = Application(settings, driver)
    set_application(application)
    driver.on_bot_connect(application.validate_bot_connection)
    driver.on_startup(application.startup)
    driver.on_shutdown(application.shutdown)
    nonebot.load_from_toml("pyproject.toml")
    nonebot.run()


if __name__ == "__main__":
    main()
