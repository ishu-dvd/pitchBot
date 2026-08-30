from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.storage import create_database_engine, create_session_factory


@pytest.fixture
def migrated_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, sessionmaker[Session]]]:
    database_path = (tmp_path / "pitchbot-test.db").as_posix()
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("PITCHBOT_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    try:
        yield database_url, create_session_factory(engine)
    finally:
        engine.dispose()
