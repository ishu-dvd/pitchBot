from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.storage import SqlAlchemyEventRepository
from pitchbot.storage.cli import main


def test_cli_exports_only_redacted_payloads(
    migrated_database: tuple[str, sessionmaker[Session]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, session_factory = migrated_database
    lead_id = uuid4()
    SqlAlchemyEventRepository(session_factory).append(
        lead_id,
        "lead",
        "lead.created",
        {"display_name": "Synthetic buyer", "category": "apparel"},
    )

    assert main(["export", str(lead_id)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported[0]["payload"] == {
        "display_name": "[REDACTED]",
        "category": "apparel",
    }


def test_cli_rejects_mismatched_destructive_confirmation(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _ = migrated_database
    lead_id = uuid4()

    with pytest.raises(SystemExit) as error:
        main(["delete", str(lead_id), "--confirm", str(uuid4())])

    assert error.value.code == 2


def test_cli_rejects_retention_cutoff_without_timezone() -> None:
    with pytest.raises(SystemExit) as error:
        main(["purge", "--cutoff", "2026-01-01T00:00:00"])

    assert error.value.code == 2
