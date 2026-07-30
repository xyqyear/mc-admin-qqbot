"""Create the initial bot schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def utc_datetime() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "player_binding",
        sa.Column("qq_id", sa.String(12), primary_key=True),
        sa.Column("player_uuid", sa.String(36), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.UniqueConstraint("player_uuid", name="uq_player_binding_player_uuid"),
    )
    op.create_table(
        "message_route",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_self_id", sa.String(12), nullable=False),
        sa.Column(
            "context_type",
            sa.Enum(
                "GROUP",
                "PRIVATE",
                name="context_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("context_id", sa.String(12), nullable=False),
        sa.Column("message_id", sa.String(32), nullable=False),
        sa.Column("server_id", sa.String(100), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.UniqueConstraint(
            "bot_self_id",
            "context_type",
            "context_id",
            "message_id",
            name="uq_message_route_scope_message",
        ),
    )
    op.create_table(
        "event_checkpoint",
        sa.Column("stream_name", sa.String(64), primary_key=True),
        sa.Column("cursor", sa.String(255), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
    )
    op.create_table(
        "outbound_delivery",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "QQ_GROUP",
                "QQ_PRIVATE",
                "MC_PRIVATE",
                name="delivery_channel",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("bot_self_id", sa.String(12)),
        sa.Column("destination_id", sa.String(32), nullable=False),
        sa.Column("target_player", sa.String(16)),
        sa.Column("server_id", sa.String(100)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("route_server_id", sa.String(100)),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SENT",
                name="delivery_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", utc_datetime(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("sent_at", utc_datetime()),
        sa.Column("remote_message_id", sa.String(32)),
        sa.Column("last_error", sa.Text()),
        sa.UniqueConstraint("dedupe_key", name="uq_outbound_delivery_dedupe_key"),
    )
    op.create_index(
        "ix_outbound_delivery_ready",
        "outbound_delivery",
        ["status", "available_at", "id"],
    )
    op.create_table(
        "restart_watch",
        sa.Column("server_id", sa.String(100), primary_key=True),
        sa.Column("requested_by_qq", sa.String(12), nullable=False),
        sa.Column("bot_self_id", sa.String(12), nullable=False),
        sa.Column(
            "context_type",
            sa.Enum(
                "GROUP",
                "PRIVATE",
                name="restart_context_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("context_id", sa.String(12), nullable=False),
        sa.Column("deadline", utc_datetime(), nullable=False),
        sa.Column("observed_unhealthy", sa.Boolean(), nullable=False),
        sa.Column("healthy_streak", sa.Integer(), nullable=False),
        sa.Column("created_at", utc_datetime(), nullable=False),
        sa.Column("updated_at", utc_datetime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("restart_watch")
    op.drop_index("ix_outbound_delivery_ready", table_name="outbound_delivery")
    op.drop_table("outbound_delivery")
    op.drop_table("event_checkpoint")
    op.drop_table("message_route")
    op.drop_table("player_binding")
