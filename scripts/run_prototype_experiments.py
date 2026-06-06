"""Batch experimental harness for the prototype LLM testing workflow.

This script runs the prototype generator/healer pipeline against one or more
source files, optionally repeating each source multiple times, and writes a
thesis-friendly summary in JSON, CSV, and Markdown formats.

Example:
  python scripts/run_prototype_experiments.py --sources target_code.py --runs 3
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.llm_prototype_harness import load_local_env, run_prototype


@dataclass
class RunRow:
    source: str
    run: int
    source_functions: int
    source_classes: int
    validation_passed: bool
    validation_issues: int
    generated_lines: int
    run1_passed: bool
    run1_duration_seconds: float
    healer_applied: bool
    final_passed: bool
    final_duration_seconds: float
    report_file: str
    test_file: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prototype LLM experiments across multiple sources.")
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["target_code.py"],
        help="One or more Python source files to benchmark",
    )
    parser.add_argument("--runs", type=int, default=1, help="Number of runs per source file")
    parser.add_argument(
        "--output-dir",
        default="tests/experiments",
        help="Directory where generated tests will be written",
    )
    parser.add_argument(
        "--report-dir",
        default="reports/prototype_experiments",
        help="Directory where per-run prototype reports will be written",
    )
    parser.add_argument(
        "--summary-json",
        default="reports/prototype_experiments_summary.json",
        help="Path to the aggregate JSON summary",
    )
    parser.add_argument(
        "--summary-csv",
        default="reports/prototype_experiments_summary.csv",
        help="Path to the aggregate CSV summary",
    )
    parser.add_argument(
        "--summary-md",
        default="reports/prototype_experiments_summary.md",
        help="Path to the aggregate Markdown summary",
    )
    return parser.parse_args()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _source_path(raw: str) -> Path:
    path = Path(raw)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {raw}")
    return path


def _generated_line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except Exception:
        return 0


def _final_result(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("run2") or report.get("run1") or {}


def _run_one(source_path: Path, run_index: int, output_dir: Path, report_dir: Path) -> RunRow:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    test_path = output_dir / f"generated_tests_{source_path.stem}_run_{run_index}.py"
    report_path = report_dir / f"prototype_run_{source_path.stem}_{run_index}.json"

    report = run_prototype(
        source=str(source_path),
        output=str(test_path),
        report_output=str(report_path),
    )

    validation = report.get("validation", {}) if isinstance(report, dict) else {}
    run1 = report.get("run1", {}) if isinstance(report, dict) else {}
    final_result = _final_result(report) if isinstance(report, dict) else {}

    analysis = report.get("analysis", {}) if isinstance(report, dict) else {}

    return RunRow(
        source=source_path.as_posix(),
        run=run_index,
        source_functions=_safe_int(analysis.get("function_count", 0)),
        source_classes=_safe_int(analysis.get("class_count", 0)),
        validation_passed=bool(validation.get("passed", False)),
        validation_issues=len(validation.get("issues", []) or []),
        generated_lines=_generated_line_count(test_path),
        run1_passed=bool(run1.get("passed", False)),
        run1_duration_seconds=_safe_float(run1.get("duration_seconds", 0.0)),
        healer_applied=bool(report.get("healer_applied", False)),
        final_passed=bool(final_result.get("passed", False)),
        final_duration_seconds=_safe_float(final_result.get("duration_seconds", 0.0)),
        report_file=report_path.as_posix(),
        test_file=test_path.as_posix(),
    )


def _write_csv(rows: list[RunRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()) if rows else [
            "source",
            "run",
            "source_functions",
            "source_classes",
            "validation_passed",
            "validation_issues",
            "generated_lines",
            "run1_passed",
            "run1_duration_seconds",
            "healer_applied",
            "final_passed",
            "final_duration_seconds",
            "report_file",
            "test_file",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(rows: list[RunRow], path: Path) -> dict[str, Any]:
    summary = _build_summary(rows)
    payload = {
        "generated_utc": summary["generated_utc"],
        "rows": [asdict(row) for row in rows],
        "summary": summary,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _build_summary(rows: list[RunRow]) -> dict[str, Any]:
    generated_utc = datetime.now(timezone.utc).isoformat()
    if not rows:
        return {
            "generated_utc": generated_utc,
            "total_runs": 0,
            "sources": [],
            "final_pass_rate": 0.0,
            "validation_pass_rate": 0.0,
            "mean_run1_duration_seconds": 0.0,
            "mean_final_duration_seconds": 0.0,
            "mean_generated_lines": 0.0,
        }

    sources = sorted({row.source for row in rows})
    total_runs = len(rows)
    final_pass_rate = round(sum(1 for row in rows if row.final_passed) / total_runs * 100.0, 2)
    validation_pass_rate = round(sum(1 for row in rows if row.validation_passed) / total_runs * 100.0, 2)
    mean_run1 = round(mean(row.run1_duration_seconds for row in rows), 3)
    mean_final = round(mean(row.final_duration_seconds for row in rows), 3)
    mean_lines = round(mean(row.generated_lines for row in rows), 2)

    return {
        "generated_utc": generated_utc,
        "total_runs": total_runs,
        "sources": sources,
        "final_pass_rate": final_pass_rate,
        "validation_pass_rate": validation_pass_rate,
        "mean_run1_duration_seconds": mean_run1,
        "mean_final_duration_seconds": mean_final,
        "mean_generated_lines": mean_lines,
    }


def _write_markdown(rows: list[RunRow], path: Path) -> None:
    summary = _build_summary(rows)
    lines = [
        "# Prototype Experiment Summary",
        "",
        f"- Sources: {', '.join(summary['sources']) if summary['sources'] else 'none'}",
        f"- Total runs: {summary['total_runs']}",
        f"- Validation pass rate: {summary['validation_pass_rate']}%",
        f"- Final pass rate: {summary['final_pass_rate']}%",
        f"- Mean first-run duration: {summary['mean_run1_duration_seconds']}s",
        f"- Mean final duration: {summary['mean_final_duration_seconds']}s",
        f"- Mean generated test length: {summary['mean_generated_lines']} lines",
        "",
        "## Per-run results",
        "",
    ]
    for row in rows:
        lines.append(
            f"- `{row.source}` run {row.run}: validation={row.validation_passed}, first_run={row.run1_passed}, final={row.final_passed}, healer={row.healer_applied}, lines={row.generated_lines}, report={row.report_file}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    load_local_env()

    sources = [_source_path(source) for source in args.sources]
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)

    rows: list[RunRow] = []
    for source_path in sources:
        for run_index in range(1, max(args.runs, 1) + 1):
            print(f"Running prototype experiment for {source_path} (run {run_index}/{args.runs})")
            rows.append(_run_one(source_path, run_index, output_dir, report_dir))

    json_path = Path(args.summary_json)
    csv_path = Path(args.summary_csv)
    md_path = Path(args.summary_md)

    payload = _write_json(rows, json_path)
    _write_csv(rows, csv_path)
    _write_markdown(rows, md_path)

    summary = payload["summary"]
    print(f"Wrote summary JSON to {json_path}")
    print(f"Wrote summary CSV to {csv_path}")
    print(f"Wrote summary Markdown to {md_path}")
    print(
        f"Final pass rate: {summary['final_pass_rate']}% | Validation pass rate: {summary['validation_pass_rate']}% | Mean final duration: {summary['mean_final_duration_seconds']}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
