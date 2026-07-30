import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import anyio
import nonebot
import pytest
from alembic.migration import MigrationContext
from sqlalchemy import inspect

import mc_admin_qqbot.__main__ as entrypoint
import mc_admin_qqbot.runtime as runtime
from alembic import command
from mc_admin_qqbot.application import Application
from mc_admin_qqbot.config import Settings
from mc_admin_qqbot.database import Database


class TaskGroupStub:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def start_soon(self, function, *args) -> None:
        self.tasks.append(asyncio.create_task(function(*args)))


async def test_application_startup_and_shutdown(
    tmp_path: Path, settings: Settings
) -> None:
    database_path = tmp_path / "nested" / "bot.db"
    settings = settings.model_copy(
        update={"database_url": f"sqlite+aiosqlite:///{database_path.as_posix()}"}
    )
    application = Application(settings, nonebot.get_driver())
    task_group = TaskGroupStub()
    application.driver = cast(Any, SimpleNamespace(task_group=task_group))
    application.database.migrate = AsyncMock()
    application.database.dispose = AsyncMock()
    application.event_consumer.run = AsyncMock()
    application.delivery_worker.run = AsyncMock()
    application.restart_monitor.run = AsyncMock()

    await application.startup()
    assert database_path.parent.is_dir()
    application.database.migrate.assert_awaited_once()
    assert len(task_group.tasks) == 3
    await asyncio.gather(*task_group.tasks)
    await application.shutdown()
    application.database.dispose.assert_awaited_once()


async def test_application_job_signals_ready_and_done_on_failure(
    settings: Settings,
) -> None:
    application = Application(settings, nonebot.get_driver())
    ready = anyio.Event()
    done = anyio.Event()

    async def fail() -> None:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await application._run_job(fail, ready, done)
    assert ready.is_set()
    assert done.is_set()
    await application.database.dispose()


async def test_application_rejects_wrong_onebot_identity(settings: Settings) -> None:
    application = Application(settings, nonebot.get_driver())
    connection = SimpleNamespace(close=AsyncMock())
    bot = cast(
        Any,
        SimpleNamespace(
            self_id="wrong",
            adapter=SimpleNamespace(connections={"wrong": connection}),
        ),
    )
    await application.validate_bot_connection(bot)
    connection.close.assert_awaited_once_with(1008, "Unexpected bot self ID")

    expected = cast(
        Any,
        SimpleNamespace(
            self_id=str(settings.qq_bot_id),
            adapter=SimpleNamespace(connections={}),
        ),
    )
    await application.validate_bot_connection(expected)
    await application.database.dispose()


def test_entrypoint_wires_nonebot(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    driver = SimpleNamespace(
        config=SimpleNamespace(),
        register_adapter=Mock(),
        on_bot_connect=Mock(),
        on_startup=Mock(),
        on_shutdown=Mock(),
    )
    application = SimpleNamespace(
        validate_bot_connection=AsyncMock(),
        startup=AsyncMock(),
        shutdown=AsyncMock(),
    )
    init = Mock()
    load = Mock()
    run = Mock()
    set_application = Mock()
    monkeypatch.setattr(entrypoint, "get_settings", lambda: settings)
    monkeypatch.setattr(entrypoint.nonebot, "init", init)
    monkeypatch.setattr(entrypoint.nonebot, "get_driver", lambda: driver)
    monkeypatch.setattr(entrypoint.nonebot, "load_from_toml", load)
    monkeypatch.setattr(entrypoint.nonebot, "run", run)
    monkeypatch.setattr(entrypoint, "Application", lambda *_args: application)
    monkeypatch.setattr(entrypoint, "set_application", set_application)

    entrypoint.main()

    assert driver.config.onebot_access_token == "onebot-test-token"
    driver.on_bot_connect.assert_called_once_with(application.validate_bot_connection)
    driver.on_startup.assert_called_once_with(application.startup)
    driver.on_shutdown.assert_called_once_with(application.shutdown)
    set_application.assert_called_once_with(application)
    load.assert_called_once_with("pyproject.toml")
    run.assert_called_once()
    assert init.call_args.kwargs["driver"] == "~fastapi+~httpx+~websockets"


def test_runtime_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_application", None)
    with pytest.raises(RuntimeError, match="not been configured"):
        runtime.get_application()


async def test_standalone_alembic_path(tmp_path: Path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{(tmp_path / 'standalone.db').as_posix()}"
    )
    config = database.alembic_config()
    await asyncio.to_thread(command.upgrade, config, "head")
    async with database.engine.connect() as connection:
        revision = await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert revision == "20260729_initial_schema"
    assert "player_binding" in tables
    await database.dispose()
