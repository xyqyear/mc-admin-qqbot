from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ContextType(StrEnum):
    GROUP = "group"
    PRIVATE = "private"


class DeliveryChannel(StrEnum):
    QQ_GROUP = "qq_group"
    QQ_PRIVATE = "qq_private"
    MC_PRIVATE = "mc_private"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"


enum_options = {"native_enum": False, "create_constraint": True}


class PlayerBinding(Base):
    __tablename__ = "player_binding"

    qq_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    player_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MessageRoute(Base):
    __tablename__ = "message_route"
    __table_args__ = (
        UniqueConstraint(
            "bot_self_id",
            "context_type",
            "context_id",
            "message_id",
            name="uq_message_route_scope_message",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_self_id: Mapped[str] = mapped_column(String(12), nullable=False)
    context_type: Mapped[ContextType] = mapped_column(
        Enum(ContextType, name="context_type", **enum_options), nullable=False
    )
    context_id: Mapped[str] = mapped_column(String(12), nullable=False)
    message_id: Mapped[str] = mapped_column(String(32), nullable=False)
    server_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class EventCheckpoint(Base):
    __tablename__ = "event_checkpoint"

    stream_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )


class OutboundDelivery(Base):
    __tablename__ = "outbound_delivery"
    __table_args__ = (
        Index("ix_outbound_delivery_ready", "status", "available_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    channel: Mapped[DeliveryChannel] = mapped_column(
        Enum(DeliveryChannel, name="delivery_channel", **enum_options), nullable=False
    )
    bot_self_id: Mapped[str | None] = mapped_column(String(12))
    destination_id: Mapped[str] = mapped_column(String(32), nullable=False)
    target_player: Mapped[str | None] = mapped_column(String(16))
    server_id: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    route_server_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status", **enum_options),
        default=DeliveryStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    remote_message_id: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)


class RestartWatch(Base):
    __tablename__ = "restart_watch"

    server_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    requested_by_qq: Mapped[str] = mapped_column(String(12), nullable=False)
    bot_self_id: Mapped[str] = mapped_column(String(12), nullable=False)
    context_type: Mapped[ContextType] = mapped_column(
        Enum(ContextType, name="restart_context_type", **enum_options), nullable=False
    )
    context_id: Mapped[str] = mapped_column(String(12), nullable=False)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observed_unhealthy: Mapped[bool] = mapped_column(Boolean, default=False)
    healthy_streak: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )
