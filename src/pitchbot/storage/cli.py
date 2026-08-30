from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pitchbot.config import Settings
from pitchbot.storage.database import create_database_engine, create_session_factory
from pitchbot.storage.repository import SqlAlchemyEventRepository, SqlAlchemyPrivacyRepository


def _confirmed(aggregate_id: UUID, confirmation: str | None) -> bool:
    return confirmation == str(aggregate_id)


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("cutoff must include a UTC offset or timezone")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PitchBot privacy and retention operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="Export a redacted aggregate journey")
    export.add_argument("aggregate_id", type=UUID)

    anonymize = subparsers.add_parser("anonymize", help="Erase event payload content")
    anonymize.add_argument("aggregate_id", type=UUID)
    anonymize.add_argument("--confirm", required=True)

    delete = subparsers.add_parser(
        "delete",
        help="Delete event payloads and close the aggregate tombstone",
    )
    delete.add_argument("aggregate_id", type=UUID)
    delete.add_argument("--confirm", required=True)

    purge = subparsers.add_parser("purge", help="Purge expired non-protected events")
    purge.add_argument("--cutoff", type=_aware_datetime, required=True)
    purge.add_argument("--execute", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    database_url = Settings().database_url
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)
    events = SqlAlchemyEventRepository(session_factory)
    privacy = SqlAlchemyPrivacyRepository(session_factory, events)

    try:
        if args.command == "export":
            print(json.dumps(privacy.export_redacted(args.aggregate_id), indent=2))
            return 0

        if args.command in {"anonymize", "delete"} and not _confirmed(
            args.aggregate_id, args.confirm
        ):
            parser.error("--confirm must exactly match aggregate_id")

        if args.command == "anonymize":
            affected = privacy.anonymize(args.aggregate_id)
            print(f"Anonymized {affected} event payloads")
            return 0

        if args.command == "delete":
            events_deleted, aggregate_heads_closed = privacy.hard_delete(args.aggregate_id)
            print(
                f"Deleted {events_deleted} events and "
                f"closed {aggregate_heads_closed} aggregate heads; suppression retained"
            )
            return 0

        if args.command == "purge":
            affected = privacy.purge_expired(args.cutoff, dry_run=not args.execute)
            mode = "execute" if args.execute else "dry-run"
            print(f"{mode}: {affected} expired events eligible")
            return 0

        parser.error("unsupported command")
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
