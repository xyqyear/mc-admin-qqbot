from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

type Scope = Literal["group", "private"]
type Channel = Literal["group", "private", "game"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qq_id: Mapped[str | None] = mapped_column(String(12))
    player_uuid: Mapped[str | None] = mapped_column(String(36), unique=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class QQIdentity(Base):
    __tablename__ = "qq_identity"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    openid: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class GroupMember(Base):
    __tablename__ = "group_member"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    group_openid: Mapped[str] = mapped_column(String, primary_key=True)
    member_openid: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    member_nickname: Mapped[str | None] = mapped_column(String)
    member_role: Mapped[str | None] = mapped_column(String)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class QQGroup(Base):
    __tablename__ = "qq_group"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    openid: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    joined: Mapped[bool] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    name_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BotPreferences(Base):
    __tablename__ = "bot_preferences"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    fallback_group: Mapped[str | None] = mapped_column(String)
    restart_timeout_seconds: Mapped[int] = mapped_column(default=600)
    restart_poll_interval_seconds: Mapped[int] = mapped_column(default=5)


class ServerGroupRule(Base):
    __tablename__ = "server_group_rule"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    server_id: Mapped[str] = mapped_column(String, primary_key=True)
    group_openid: Mapped[str | None] = mapped_column(String)


class BindRequest(Base):
    __tablename__ = "bind_request"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    player_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    player_name: Mapped[str] = mapped_column(String(16))
    server_id: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MessageRoute(Base):
    __tablename__ = "message_route"

    app_id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[Scope] = mapped_column(String, primary_key=True)
    destination: Mapped[str] = mapped_column(String, primary_key=True)
    ref_idx: Mapped[str] = mapped_column(String, primary_key=True)
    server_id: Mapped[str] = mapped_column(String)


class EventCheckpoint(Base):
    __tablename__ = "event_checkpoint"

    stream_name: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str] = mapped_column(String)


class OutboundDelivery(Base):
    __tablename__ = "outbound_delivery"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True)
    channel: Mapped[Channel] = mapped_column(String)
    destination: Mapped[str] = mapped_column(String)
    body: Mapped[str | bytes] = mapped_column(Text)
    server_id: Mapped[str | None] = mapped_column(String)
    reply_msg_id: Mapped[str | None] = mapped_column(Text)
    reply_seq: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    remote_message_id: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class RestartWatch(Base):
    __tablename__ = "restart_watch"

    server_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[Scope] = mapped_column(String)
    destination: Mapped[str] = mapped_column(String)
    reply_msg_id: Mapped[str] = mapped_column(Text)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime())
