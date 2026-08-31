from __future__ import annotations

import html
import os
import tempfile
from pathlib import Path

from pitchbot.benchmarks.manifest import load_json_model
from pitchbot.benchmarks.models import EvaluationMetric, EvaluationRun


def validate_evaluation_run(path: Path) -> EvaluationRun:
    return load_json_model(path, EvaluationRun)


def ensure_distinct_files(input_path: Path, output_path: Path) -> None:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input artifact and output report must be different files")
    if output_path.exists() and os.path.samefile(input_path, output_path):
        raise ValueError("input artifact and output report must be different files")


def _metric_row(metric: EvaluationMetric) -> str:
    threshold = "-"
    outcome = "informational"
    result = metric.meets_threshold()
    if result is not None:
        assert metric.threshold is not None
        threshold = f"{metric.direction.value} {metric.threshold:g}"
        outcome = "pass" if result else "fail"
    values = (metric.name, f"{metric.value:g}", metric.unit, threshold, outcome)
    return "<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in values) + "</tr>"


def render_evaluation_report(run: EvaluationRun) -> str:
    case_rows = []
    for case in run.cases:
        failed_gates = sum(metric.meets_threshold() is False for metric in case.metrics)
        case_rows.append(
            "<tr>"
            f"<td>{html.escape(case.case_id)}</td>"
            f"<td>{html.escape(case.status.value)}</td>"
            f"<td>{html.escape(case.language.value)}</td>"
            f"<td>{html.escape(case.industry)}</td>"
            f"<td>{html.escape(case.persona)}</td>"
            f"<td>{case.duration_ms:g}</td>"
            f"<td>{failed_gates}</td>"
            "</tr>"
        )
    metric_rows = [_metric_row(metric) for metric in run.metrics]
    gate_status = "pass" if run.gates_pass() else "fail or incomplete"
    completed = run.completed_at.isoformat() if run.completed_at is not None else "in progress"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PitchBot evaluation {html.escape(run.run_id)}</title>
<style>
body {{ color: #172033; font: 14px system-ui, sans-serif; margin: 2rem; }}
.summary {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
.card {{ background: #f3f6fa; border-radius: .4rem; padding: .75rem 1rem; }}
table {{ border-collapse: collapse; margin: 1rem 0 2rem; width: 100%; }}
th, td {{ border-bottom: 1px solid #ccd4df; padding: .5rem; text-align: left; }}
th {{ background: #e8edf4; }}
</style>
</head>
<body>
<h1>PitchBot evaluation</h1>
<div class="summary">
<div class="card"><strong>Run</strong><br>{html.escape(run.run_id)}</div>
<div class="card"><strong>Status</strong><br>{html.escape(run.status.value)}</div>
<div class="card"><strong>Artifact gates</strong><br>{gate_status}</div>
<div class="card"><strong>Cases</strong><br>{len(run.cases)}</div>
</div>
<p>Revision: <code>{html.escape(run.git_revision)}</code><br>
Suite: {html.escape(run.suite_id)} {html.escape(run.suite_version)}<br>
Corpus: {html.escape(run.corpus_id)} {html.escape(run.corpus_version)}<br>
Started: {html.escape(run.started_at.isoformat())}<br>
Completed: {html.escape(completed)}</p>
<h2>Run metrics</h2>
<table><thead><tr><th>Metric</th><th>Value</th><th>Unit</th><th>Gate</th><th>Outcome</th></tr></thead>
<tbody>{"".join(metric_rows)}</tbody></table>
<h2>Cases</h2>
<table><thead><tr><th>Case</th><th>Status</th><th>Language</th><th>Industry</th>
<th>Persona</th><th>Duration (ms)</th><th>Failed gates</th></tr></thead>
<tbody>{"".join(case_rows)}</tbody></table>
</body>
</html>
"""


def write_text_atomically(output_path: Path, content: str, *, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
        if overwrite:
            temporary_path.replace(output_path)
        else:
            os.link(temporary_path, output_path)
            temporary_path.unlink()
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def write_evaluation_report(run: EvaluationRun, output_path: Path, *, overwrite: bool) -> None:
    write_text_atomically(output_path, render_evaluation_report(run), overwrite=overwrite)
