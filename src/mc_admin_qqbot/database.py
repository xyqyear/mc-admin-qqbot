from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, event, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import (
    Base,
    BotDefaultServer,
    BotPreferences,
    EventCheckpoint,
    GroupMember,
    MessageRoute,
    OutboundDelivery,
    QQGroup,
    QQIdentity,
    RestartWatch,
    Scope,
    ServerGroupRule,
    User,
    utc_now,
)
from .qq import GROUP_MESSAGE_EVENTS, QQMessage


@dataclass(frozen=True)
class GroupRoutes:
    fallback: str | None
    rules: dict[str, str | None]
    groups: dict[str, QQGroup]

    def destination(self, server_id: str) -> str | None:
        openid = self.rules.get(server_id, self.fallback)
        group = self.groups.get(openid) if openid else None
        return group.openid if group and group.joined else None


class Database:
    def __init__(self, url: str, app_id: str) -> None:
        self.path = Path(str(make_url(url).database))
        self.app_id = app_id
        self.engine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                insert(BotPreferences)
                .values(app_id=self.app_id)
                .on_conflict_do_nothing()
            )

    async def close(self) -> None:
        await self.engine.dispose()

    async def preferences(self, session: AsyncSession) -> BotPreferences:
        preferences = await session.get(BotPreferences, self.app_id)
        assert preferences is not None
        return preferences

    async def default_server(self, session: AsyncSession) -> str | None:
        return await session.scalar(
            select(BotDefaultServer.server_id).where(
                BotDefaultServer.app_id == self.app_id
            )
        )

    async def save_default_server(self, server_id: str | None) -> None:
        async with self.sessions.begin() as session:
            if server_id is None:
                await session.execute(
                    delete(BotDefaultServer).where(
                        BotDefaultServer.app_id == self.app_id
                    )
                )
            else:
                await session.merge(
                    BotDefaultServer(app_id=self.app_id, server_id=server_id)
                )

    async def group_routes(self, session: AsyncSession) -> GroupRoutes:
        preferences = await self.preferences(session)
        rules = await session.scalars(
            select(ServerGroupRule).where(ServerGroupRule.app_id == self.app_id)
        )
        groups = await session.scalars(
            select(QQGroup).where(QQGroup.app_id == self.app_id)
        )
        return GroupRoutes(
            preferences.fallback_group,
            {rule.server_id: rule.group_openid for rule in rules},
            {group.openid: group for group in groups},
        )

    async def observe_group(self, openid: str, event: str, name: str | None) -> bool:
        now = utc_now()
        async with self.sessions.begin() as session:
            group = await session.get(QQGroup, (self.app_id, openid))
            left = event == "GROUP_DEL_ROBOT"
            if group is None:
                group = QQGroup(app_id=self.app_id, openid=openid, joined=not left)
                session.add(group)
            elif left:
                group.joined = False
            elif not group.joined and (
                event == "GROUP_ADD_ROBOT" or event in GROUP_MESSAGE_EVENTS
            ):
                group.joined = True
                group.name_checked_at = None
            group.last_seen_at = now
            if name:
                group.name = name
                group.name_checked_at = now
            elif group.joined and (
                group.name_checked_at is None
                or now - group.name_checked_at >= timedelta(minutes=10)
            ):
                group.name_checked_at = now
                return True
        return False

    async def save_group_name(self, openid: str, name: str) -> None:
        async with self.sessions.begin() as session:
            group = await session.get(QQGroup, (self.app_id, openid))
            if group and group.joined:
                group.name = name

    async def is_superuser(self, openid: str) -> bool:
        async with self.sessions() as session:
            return bool(
                await session.scalar(
                    select(User.is_superuser)
                    .join(QQIdentity)
                    .where(
                        QQIdentity.app_id == self.app_id, QQIdentity.openid == openid
                    )
                )
            )

    async def group_member_role(self, group: str, openid: str) -> str | None:
        async with self.sessions() as session:
            member = await session.get(GroupMember, (self.app_id, group, openid))
            return member.member_role if member else None

    async def group_sender_name(self, message: QQMessage) -> str:
        if card := " ".join((message.author.get("member_nickname") or "").split()):
            return card
        async with self.sessions() as session:
            card, username = (
                await session.execute(
                    select(GroupMember.member_nickname, QQIdentity.username)
                    .join(
                        QQIdentity,
                        (QQIdentity.app_id == GroupMember.app_id)
                        & (QQIdentity.openid == GroupMember.member_openid),
                    )
                    .where(
                        GroupMember.app_id == self.app_id,
                        GroupMember.group_openid == message.destination,
                        GroupMember.member_openid == message.openid,
                    )
                )
            ).one()
        return (
            " ".join((card or "").split())
            or " ".join((username or "").split())
            or "QQ用户"
        )

    async def observe(self, message: QQMessage) -> User:
        async with self.sessions.begin() as session:
            identity = await session.get(QQIdentity, (self.app_id, message.openid))
            if identity is None:
                user = User()
                session.add(user)
                await session.flush()
                identity = QQIdentity(
                    app_id=self.app_id,
                    openid=message.openid,
                    user_id=user.id,
                    profile={},
                )
                session.add(identity)
            else:
                user = await session.get(User, identity.user_id)
                assert user is not None
            author = message.author
            identity.profile = {
                **identity.profile,
                **{
                    key: value
                    for key, value in author.items()
                    if value not in (None, "")
                },
            }
            if author.get("username"):
                identity.username = author["username"]
            if author.get("avatar"):
                identity.avatar_url = author["avatar"]
            identity.last_seen_at = utc_now()
            if message.scope == "group":
                key = (self.app_id, message.destination, message.openid)
                member = await session.get(GroupMember, key)
                if member is None:
                    member = GroupMember(
                        app_id=self.app_id,
                        group_openid=message.destination,
                        member_openid=message.openid,
                        user_id=user.id,
                    )
                    session.add(member)
                if "member_nickname" in author:
                    member.member_nickname = author["member_nickname"]
                if "member_role" in author:
                    member.member_role = author["member_role"]
                member.last_seen_at = utc_now()
            return user

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        *,
        key: str,
        channel: str,
        destination: str,
        body: str | bytes,
        server_id: str | None = None,
        reply_msg_id: str | None = None,
        reply_seq: int = 1,
    ) -> None:
        await session.execute(
            insert(OutboundDelivery)
            .values(
                dedupe_key=key,
                channel=channel,
                destination=destination,
                body=body,
                server_id=server_id,
                reply_msg_id=reply_msg_id,
                reply_seq=reply_seq,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )

    async def reply(self, message: QQMessage, body: str | bytes) -> None:
        async with self.sessions.begin() as session:
            await self.enqueue(
                session,
                key=f"reply:{self.app_id}:{message.id}",
                channel=message.scope,
                destination=message.destination,
                body=body,
                reply_msg_id=message.id,
            )

    def route_statement(
        self, scope: Scope, destination: str, ref_idx: str, server_id: str
    ):
        statement = insert(MessageRoute).values(
            app_id=self.app_id,
            scope=scope,
            destination=destination,
            ref_idx=ref_idx,
            server_id=server_id,
        )
        return statement.on_conflict_do_update(
            index_elements=["app_id", "scope", "destination", "ref_idx"],
            set_={"server_id": server_id},
        )

    async def record_route(self, message: QQMessage, server_id: str) -> None:
        if message.msg_idx:
            async with self.sessions.begin() as session:
                await session.execute(
                    self.route_statement(
                        message.scope, message.destination, message.msg_idx, server_id
                    )
                )

    async def route_for(self, message: QQMessage) -> str | None:
        async with self.sessions() as session:
            row = await session.get(
                MessageRoute,
                (self.app_id, message.scope, message.destination, message.ref_msg_idx),
            )
            return row.server_id if row else None

    async def checkpoint(self) -> str | None:
        async with self.sessions() as session:
            row = await session.get(EventCheckpoint, "mc-admin-chat")
            return row.cursor if row else None

    @staticmethod
    async def advance(session: AsyncSession, cursor: str) -> None:
        statement = insert(EventCheckpoint).values(
            stream_name="mc-admin-chat", cursor=cursor
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["stream_name"], set_={"cursor": cursor}
            )
        )

    async def pending(self, limit: int = 20) -> list[OutboundDelivery]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(OutboundDelivery)
                    .where(
                        OutboundDelivery.status == "pending",
                        OutboundDelivery.available_at <= utc_now(),
                    )
                    .order_by(OutboundDelivery.id)
                    .limit(limit)
                )
            )

    async def delivered(
        self,
        delivery: OutboundDelivery,
        message_id: str | None = None,
        ref_idx: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(OutboundDelivery, delivery.id)
            assert row is not None
            row.status = "sent"
            row.destination = delivery.destination
            row.remote_message_id = message_id
            row.last_error = None
            if isinstance(row.body, bytes):
                row.body = ""
            if ref_idx and delivery.server_id and delivery.channel != "game":
                await session.execute(
                    self.route_statement(
                        delivery.channel,
                        delivery.destination,
                        ref_idx,
                        delivery.server_id,
                    )
                )

    async def failed(
        self, delivery: OutboundDelivery, error: str, *, permanent: bool = False
    ) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(OutboundDelivery, delivery.id)
            assert row is not None
            row.attempts += 1
            row.last_error = error
            if permanent:
                row.status = "failed"
            else:
                row.available_at = utc_now() + timedelta(
                    seconds=min(30, 2 ** min(row.attempts, 5))
                )

    async def watches(self) -> list[RestartWatch]:
        async with self.sessions() as session:
            return list(await session.scalars(select(RestartWatch)))
