from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from pitchbot.storage import create_database_engine


def test_migration_creates_expected_schema(
    migrated_database: tuple[str, object],
) -> None:
    database_url, _ = migrated_database
    engine = create_database_engine(database_url)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "aggregate_records",
            "alembic_version",
            "event_records",
            "privacy_operation_records",
            "suppression_records",
        }
        assert {column["name"] for column in inspector.get_columns("aggregate_records")} == {
            "aggregate_id",
            "aggregate_type",
            "created_at",
            "current_version",
            "privacy_state",
            "updated_at",
        }
        assert {column["name"] for column in inspector.get_columns("event_records")} == {
            "aggregate_id",
            "aggregate_type",
            "aggregate_version",
            "anonymized_at",
            "event_id",
            "event_type",
            "occurred_at",
            "payload",
            "sequence",
        }
        assert {column["name"] for column in inspector.get_columns("suppression_records")} == {
            "channel",
            "lead_id",
            "occurred_at",
            "reason",
            "sequence",
            "suppressed",
            "suppression_id",
        }
        assert {
            column["name"] for column in inspector.get_columns("privacy_operation_records")
        } == {
            "affected_event_count",
            "aggregate_id",
            "occurred_at",
            "operation",
            "operation_id",
            "sequence",
        }
        event_uniques = {
            constraint["name"] for constraint in inspector.get_unique_constraints("event_records")
        }
        assert "uq_event_aggregate_version" in event_uniques
        assert {index["name"] for index in inspector.get_indexes("event_records")} == {
            "ix_event_aggregate_sequence",
            "ix_event_occurred_at",
        }
        assert {index["name"] for index in inspector.get_indexes("suppression_records")} == {
            "ix_suppression_lead_channel_sequence"
        }
        assert {index["name"] for index in inspector.get_indexes("privacy_operation_records")} == {
            "ix_privacy_operation_aggregate_sequence"
        }
    finally:
        engine.dispose()


def test_migration_matches_sqlalchemy_metadata(
    migrated_database: tuple[str, object],
) -> None:
    database_url, _ = migrated_database
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.check(config)
