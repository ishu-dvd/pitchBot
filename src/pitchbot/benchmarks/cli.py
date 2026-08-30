from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pitchbot.benchmarks.environment import capture_hardware_profile
from pitchbot.benchmarks.manifest import (
    canonical_manifest_sha256,
    validate_candidate_registry,
    validate_corpus_manifest,
)
from pitchbot.benchmarks.metrics import character_error_rate, word_error_rate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PitchBot benchmark utilities")
    commands = parser.add_subparsers(dest="command", required=True)

    candidates = commands.add_parser("validate-candidates")
    candidates.add_argument("path", type=Path)

    corpus = commands.add_parser("validate-corpus")
    corpus.add_argument("path", type=Path)

    transcript = commands.add_parser("score-transcript")
    transcript.add_argument("--reference", required=True)
    transcript.add_argument("--hypothesis", required=True)

    commands.add_parser("environment")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-candidates":
        registry = validate_candidate_registry(args.path)
        print(f"validated {len(registry.candidates)} candidates")
        return 0
    if args.command == "validate-corpus":
        manifest = validate_corpus_manifest(args.path)
        digest = canonical_manifest_sha256(args.path)
        print(f"validated {len(manifest.items)} items; canonical_sha256={digest}")
        return 0
    if args.command == "score-transcript":
        if len(args.reference) > 100_000 or len(args.hypothesis) > 100_000:
            raise ValueError("transcript input exceeds size limit")
        print(
            json.dumps(
                {
                    "wer": word_error_rate(args.reference, args.hypothesis),
                    "cer": character_error_rate(args.reference, args.hypothesis),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "environment":
        print(capture_hardware_profile().model_dump_json(indent=2))
        return 0
    raise RuntimeError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
