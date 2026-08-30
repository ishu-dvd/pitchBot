"""Create append-only domain storage.

Revision ID: 0001_domain_storage
Revises:
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_domain_storage"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aggregate_records",
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("privacy_state", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("aggregate_id"),
    )
    op.create_table(
        "event_records",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_event_aggregate_version",
        ),
    )
    op.create_index(
        "ix_event_aggregate_sequence",
        "event_records",
        ["aggregate_id", "sequence"],
        unique=False,
    )

    op.create_table(
        "privacy_operation_records",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=50), nullable=False),
        sa.Column("affected_event_count", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_privacy_operation_aggregate_sequence",
        "privacy_operation_records",
        ["aggregate_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_event_occurred_at",
        "event_records",
        ["occurred_at"],
        unique=False,
    )

    op.create_table(
        "suppression_records",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("suppression_id", sa.String(length=36), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("suppression_id"),
    )
    op.create_index(
        "ix_suppression_lead_channel_sequence",
        "suppression_records",
        ["lead_id", "channel", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_privacy_operation_aggregate_sequence",
        table_name="privacy_operation_records",
    )
    op.drop_table("privacy_operation_records")
    op.drop_index(
        "ix_suppression_lead_channel_sequence",
        table_name="suppression_records",
    )
    op.drop_table("suppression_records")
    op.drop_index("ix_event_occurred_at", table_name="event_records")
    op.drop_index("ix_event_aggregate_sequence", table_name="event_records")
    op.drop_table("event_records")
    op.drop_table("aggregate_records")
