import asyncio
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import StatementError

from mc_admin_qqbot.database import Database
from mc_admin_qqbot.models import (
    Base,
    ContextType,
    DeliveryChannel,
    DeliveryStatus,
    OutboundDelivery,
    PlayerBinding,
    RestartWatch,
    utc_now,
)
from mc_admin_qqbot.repositories import BindingResult, Repository


async def test_migration_creates_all_tables_at_named_head(database: Database) -> None:
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        revision = await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )
    assert set(tables) == {
        "alembic_version",
        "event_checkpoint",
        "message_route",
        "outbound_delivery",
        "player_binding",
        "restart_watch",
    }
    assert revision == "20260729_initial_schema"
    scripts = ScriptDirectory.from_config(database.alembic_config())
    assert scripts.get_heads() == ["20260729_initial_schema"]


async def test_migration_matches_model_metadata(database: Database) -> None:
    async with database.engine.connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync), Base.metadata
            )
        )
    assert differences == []


async def test_bindings_enforce_both_sides(database: Database) -> None:
    repository = Repository(database.sessions)
    assert await repository.bind("12345", "uuid-a") == BindingResult.CREATED
    assert await repository.bind("54321", "uuid-a") == BindingResult.PLAYER_EXISTS
    assert await repository.bind("12345", "uuid-b") == BindingResult.QQ_EXISTS
    assert (await repository.binding_for_qq("12345")).player_uuid == "uuid-a"  # type: ignore[union-attr]
    assert (await repository.binding_for_player("uuid-a")).qq_id == "12345"  # type: ignore[union-attr]
    assert await repository.unbind_player("missing") is None
    assert await repository.unbind_player("uuid-a") == "12345"
    assert await repository.binding_for_qq("12345") is None


async def test_concurrent_binding_has_one_winner(database: Database) -> None:
    first = Repository(database.sessions)
    second = Repository(database.sessions)
    results = await asyncio.gather(
        first.bind("12345", "uuid-a"),
        second.bind("54321", "uuid-a"),
    )
    assert sorted(results) == [BindingResult.CREATED, BindingResult.PLAYER_EXISTS]


async def test_routes_are_scoped_and_upserted(database: Database) -> None:
    repository = Repository(database.sessions)
    values = {
        "bot_self_id": "10000",
        "context_type": ContextType.GROUP,
        "context_id": "20000",
        "message_id": "10",
    }
    await repository.record_route(**values, server_id="vanilla")
    assert await repository.route_for(**values) == "vanilla"
    await repository.record_route(**values, server_id="creative")
    assert await repository.route_for(**values) == "creative"
    assert await repository.route_for(**{**values, "context_id": "different"}) is None


async def test_delivery_retry_send_and_route(database: Database) -> None:
    repository = Repository(database.sessions)
    async with database.sessions.begin() as session:
        session.add(
            OutboundDelivery(
                dedupe_key="relay:1",
                channel=DeliveryChannel.QQ_GROUP,
                bot_self_id="10000",
                destination_id="20000",
                body="hello",
                route_server_id="vanilla",
                status=DeliveryStatus.PENDING,
                available_at=utc_now(),
            )
        )
    delivery = (await repository.pending_deliveries())[0]
    await repository.mark_delivery_failed(delivery.id, "temporary")
    assert await repository.pending_deliveries() == []
    async with database.sessions.begin() as session:
        row = await session.get(OutboundDelivery, delivery.id)
        assert row is not None
        row.available_at = utc_now() - timedelta(seconds=1)
    delivery = (await repository.pending_deliveries())[0]
    await repository.mark_delivery_sent(delivery.id, "99")
    assert await repository.pending_deliveries() == []
    assert (
        await repository.route_for(
            bot_self_id="10000",
            context_type=ContextType.GROUP,
            context_id="20000",
            message_id="99",
        )
        == "vanilla"
    )


async def test_delivery_and_watch_missing_rows_are_noops(database: Database) -> None:
    repository = Repository(database.sessions)
    await repository.mark_delivery_sent(999, None)
    await repository.mark_delivery_failed(999, "missing")
    await repository.update_restart_watch(
        "missing", observed_unhealthy=True, healthy_streak=1
    )

    async with database.sessions.begin() as session:
        delivery = OutboundDelivery(
            dedupe_key="no-route",
            channel=DeliveryChannel.MC_PRIVATE,
            destination_id="Alice",
            body="message",
            status=DeliveryStatus.PENDING,
            available_at=utc_now(),
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    await repository.mark_delivery_sent(delivery_id, None)
    assert await repository.pending_deliveries() == []


async def test_restart_watch_lifecycle(database: Database) -> None:
    repository = Repository(database.sessions)
    watch = RestartWatch(
        server_id="vanilla",
        requested_by_qq="30000",
        bot_self_id="10000",
        context_type=ContextType.PRIVATE,
        context_id="30000",
        deadline=utc_now() + timedelta(minutes=10),
        observed_unhealthy=False,
        healthy_streak=0,
    )
    assert await repository.add_restart_watch(watch)
    assert not await repository.add_restart_watch(
        RestartWatch(
            server_id="vanilla",
            requested_by_qq="30000",
            bot_self_id="10000",
            context_type=ContextType.PRIVATE,
            context_id="30000",
            deadline=watch.deadline,
            observed_unhealthy=False,
            healthy_streak=0,
        )
    )
    await repository.update_restart_watch(
        "vanilla", observed_unhealthy=True, healthy_streak=2
    )
    current = (await repository.restart_watches())[0]
    assert current.observed_unhealthy
    assert current.healthy_streak == 2
    await repository.delete_restart_watch("vanilla")
    assert await repository.restart_watches() == []


async def test_migration_downgrade_and_upgrade(database: Database) -> None:
    from alembic import command

    config = database.alembic_config()
    async with database.engine.begin() as connection:
        await connection.run_sync(
            lambda sync: (
                config.attributes.__setitem__("connection", sync),
                command.downgrade(config, "base"),
            )
        )
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert tables == ["alembic_version"]
    await database.migrate()
    async with database.sessions() as session:
        assert list(await session.scalars(select(OutboundDelivery))) == []


async def test_migration_rejects_multiple_heads(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = type("Script", (), {"get_heads": lambda self: ["one", "two"]})()
    monkeypatch.setattr(
        "mc_admin_qqbot.database.ScriptDirectory.from_config", lambda _config: script
    )
    with pytest.raises(RuntimeError, match="exactly one head"):
        await database.migrate()


async def test_sqlite_pragmas_and_session_context(database: Database) -> None:
    async with database.session() as session:
        foreign_keys = await session.scalar(text("PRAGMA foreign_keys"))
        journal_mode = await session.scalar(text("PRAGMA journal_mode"))
        busy_timeout = await session.scalar(text("PRAGMA busy_timeout"))
    assert foreign_keys == 1
    assert str(journal_mode).casefold() == "wal"
    assert busy_timeout == 5000


async def test_datetime_is_normalized_and_naive_values_fail(database: Database) -> None:
    local_time = datetime(2026, 7, 29, 12, tzinfo=timezone(timedelta(hours=8)))
    async with database.sessions.begin() as session:
        session.add(
            PlayerBinding(qq_id="12345", player_uuid="uuid-a", created_at=local_time)
        )
    binding = await Repository(database.sessions).binding_for_qq("12345")
    assert binding is not None
    assert binding.created_at == datetime(2026, 7, 29, 4, tzinfo=UTC)

    async with database.sessions() as session:
        session.add(
            PlayerBinding(
                qq_id="54321",
                player_uuid="uuid-b",
                created_at=datetime(2026, 7, 29, 12),
            )
        )
        with pytest.raises(StatementError, match="Naive datetime"):
            await session.flush()
        await session.rollback()


def test_revision_ids_are_readable_and_bounded(database: Database) -> None:
    revisions = list(
        ScriptDirectory.from_config(database.alembic_config()).walk_revisions()
    )
    assert revisions
    for revision in revisions:
        assert re.fullmatch(r"\d{8}_[a-z0-9_]+", revision.revision)
        assert len(revision.revision) <= 32
