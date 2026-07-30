from collections.abc import Awaitable, Callable
from typing import cast

import anyio
from anyio import CancelScope
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
from nonebot.drivers import Driver
from nonebot.log import logger

from .config import Settings
from .database import Database
from .game_commands import build_game_command_router
from .mcadmin import McAdminClient
from .repositories import Repository
from .services import build_command_services
from .services.events import EventProcessor, GameRelayService
from .workers import DeliveryWorker, EventConsumer, RestartMonitor


class Application:
    def __init__(self, settings: Settings, driver: Driver) -> None:
        self.settings = settings
        self.driver = driver
        self.database = Database(settings.database_url)
        self.repository = Repository(self.database.sessions)
        self.client = McAdminClient(
            driver,
            settings.mc_admin_api_url,
            settings.mc_admin_master_token.get_secret_value(),
        )
        self.commands = build_command_services(settings, self.client, self.repository)
        processor = EventProcessor(
            settings,
            self.repository,
            build_game_command_router(),
            GameRelayService(settings),
        )
        self.event_consumer = EventConsumer(self.client, self.repository, processor)
        self.delivery_worker = DeliveryWorker(
            self.client, self.repository, settings.qq_bot_id
        )
        self.restart_monitor = RestartMonitor(settings, self.client, self.repository)
        self._scopes: list[CancelScope] = []
        self._done_events: list[anyio.Event] = []

    async def validate_bot_connection(self, bot: OneBotV11Bot) -> None:
        if bot.self_id == str(self.settings.qq_bot_id):
            return
        logger.critical(
            "rejecting OneBot connection with unexpected self_id={}", bot.self_id
        )
        adapter = cast(OneBotV11Adapter, bot.adapter)
        connection = adapter.connections.get(bot.self_id)
        if connection is not None:
            await connection.close(1008, "Unexpected bot self ID")

    async def startup(self) -> None:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        await self.database.migrate()
        jobs = [
            self.event_consumer.run,
            self.delivery_worker.run,
            self.restart_monitor.run,
        ]
        ready_events: list[anyio.Event] = []
        for job in jobs:
            ready = anyio.Event()
            done = anyio.Event()
            ready_events.append(ready)
            self._done_events.append(done)
            self.driver.task_group.start_soon(self._run_job, job, ready, done)
        for ready in ready_events:
            await ready.wait()

    async def _run_job(
        self,
        job: Callable[[], Awaitable[None]],
        ready: anyio.Event,
        done: anyio.Event,
    ) -> None:
        with CancelScope() as scope:
            self._scopes.append(scope)
            ready.set()
            try:
                await job()
            finally:
                done.set()

    async def shutdown(self) -> None:
        for scope in self._scopes:
            scope.cancel()
        with anyio.move_on_after(5):
            for done in self._done_events:
                await done.wait()
        await self.database.dispose()
