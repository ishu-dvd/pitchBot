from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AggregateRecord(Base):
    __tablename__ = "aggregate_records"

    aggregate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    privacy_state: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventRecord(Base):
    __tablename__ = "event_records"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_event_aggregate_version",
        ),
        Index("ix_event_aggregate_sequence", "aggregate_id", "sequence"),
        Index("ix_event_occurred_at", "occurred_at"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuppressionRecord(Base):
    __tablename__ = "suppression_records"
    __table_args__ = (
        Index(
            "ix_suppression_lead_channel_sequence",
            "lead_id",
            "channel",
            "sequence",
        ),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suppression_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    lead_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrivacyOperationRecord(Base):
    __tablename__ = "privacy_operation_records"
    __table_args__ = (
        Index(
            "ix_privacy_operation_aggregate_sequence",
            "aggregate_id",
            "sequence",
        ),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    affected_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
