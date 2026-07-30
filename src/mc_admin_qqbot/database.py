from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command


class Database:
    def __init__(self, url: str, project_root: Path | None = None) -> None:
        self.url = url
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.engine = create_async_engine(url)
        self.sessions = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )
        self._configure_sqlite(self.engine)

    @staticmethod
    def _configure_sqlite(engine: AsyncEngine) -> None:
        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    def alembic_config(self) -> Config:
        config = Config(str(self.project_root / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.url)
        return config

    @staticmethod
    def _upgrade(connection: Connection, config: Config) -> None:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async def migrate(self) -> None:
        config = self.alembic_config()
        script = ScriptDirectory.from_config(config)
        if len(script.get_heads()) != 1:
            raise RuntimeError("The Alembic graph must contain exactly one head")
        async with self.engine.begin() as connection:
            await connection.run_sync(self._upgrade, config)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
