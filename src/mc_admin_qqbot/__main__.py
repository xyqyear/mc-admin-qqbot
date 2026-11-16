import asyncio
import logging

import aiohttp

from .application import Application
from .config import Settings
from .database import Database
from .mcadmin import McAdminClient
from .qq import QQClient


async def run(settings: Settings) -> None:
    database = Database(settings.database_url, settings.qq_app_id)
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            trust_env=True,
        ) as http:
            qq = QQClient(
                http, settings.qq_app_id, settings.qq_app_secret.get_secret_value()
            )
            mc = McAdminClient(
                http,
                settings.mc_admin_api_url,
                settings.mc_admin_master_token.get_secret_value(),
            )
            await Application(settings, database, qq, mc).run()
    finally:
        await database.close()


def main() -> None:
    settings = Settings.model_validate({})
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
