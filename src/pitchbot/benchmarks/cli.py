from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pitchbot.benchmarks.environment import capture_hardware_profile
from pitchbot.benchmarks.evaluation import (
    ensure_distinct_files,
    validate_evaluation_run,
    write_evaluation_report,
    write_text_atomically,
)
from pitchbot.benchmarks.manifest import (
    canonical_manifest_sha256,
    validate_candidate_registry,
    validate_corpus_manifest,
)
from pitchbot.benchmarks.metrics import character_error_rate, word_error_rate
from pitchbot.benchmarks.models import EvaluationRun
from pitchbot.benchmarks.retrieval import (
    run_retrieval_evaluation,
    validate_retrieval_suite,
)


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

    evaluation = commands.add_parser("validate-evaluation")
    evaluation.add_argument("path", type=Path)

    report = commands.add_parser("render-evaluation")
    report.add_argument("path", type=Path)
    report.add_argument("output", type=Path)
    report.add_argument("--force", action="store_true")

    schema = commands.add_parser("evaluation-schema")
    schema.add_argument("--output", type=Path)
    schema.add_argument("--force", action="store_true")
    retrieval_suite = commands.add_parser("validate-retrieval-suite")
    retrieval_suite.add_argument("path", type=Path)
    retrieval_run = commands.add_parser("run-retrieval")
    retrieval_run.add_argument("path", type=Path)
    retrieval_run.add_argument("output", type=Path)
    retrieval_run.add_argument("--run-id", required=True)
    retrieval_run.add_argument("--git-revision", required=True)
    retrieval_run.add_argument("--force", action="store_true")
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
    if args.command == "validate-evaluation":
        run = validate_evaluation_run(args.path)
        gates = "pass" if run.gates_pass() else "fail-or-incomplete"
        print(f"validated {len(run.cases)} cases; artifact-gates={gates}")
        return 0
    if args.command == "render-evaluation":
        run = validate_evaluation_run(args.path)
        ensure_distinct_files(args.path, args.output)
        write_evaluation_report(run, args.output, overwrite=args.force)
        print(f"rendered evaluation report: {args.output}")
        return 0
    if args.command == "evaluation-schema":
        rendered_schema = json.dumps(EvaluationRun.model_json_schema(), indent=2, sort_keys=True)
        if args.output is None:
            print(rendered_schema)
        else:
            write_text_atomically(
                args.output,
                f"{rendered_schema}\n",
                overwrite=args.force,
            )
            print(f"rendered evaluation schema: {args.output}")
        return 0
    if args.command == "validate-retrieval-suite":
        suite = validate_retrieval_suite(args.path)
        print(f"validated {len(suite.cases)} retrieval cases")
        return 0
    if args.command == "run-retrieval":
        run = run_retrieval_evaluation(
            args.path,
            run_id=args.run_id,
            git_revision=args.git_revision,
        )
        write_text_atomically(
            args.output,
            f"{run.model_dump_json(indent=2)}\n",
            overwrite=args.force,
        )
        gates = "pass" if run.gates_pass() else "fail"
        print(f"completed {len(run.cases)} retrieval cases; artifact-gates={gates}")
        return 0
    if args.command == "environment":
        print(capture_hardware_profile().model_dump_json(indent=2))
        return 0
    raise RuntimeError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
