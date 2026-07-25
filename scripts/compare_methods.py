from __future__ import annotations

"""Legacy diagnostic only; use run_research_experiment.py for thesis evidence."""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare agentic testing against traditional continuous testing."
    )
    parser.add_argument("--source", default="target_code.py", help="Target module analyzed by the agentic pipeline")
    parser.add_argument("--runs", type=int, default=1, help="Number of iterations per strategy")
    parser.add_argument("--base-ref", default="HEAD~1", help="Git base ref used by predictive selection")
    parser.add_argument("--max-heal-attempts", type=int, default=2, help="Max self-heal retries in agentic mode")
    parser.add_argument("--model", default="gemini-2.5-flash", help="LLM model used by the generation agent")
    parser.add_argument(
        "--report-output",
        default="reports/comparison_report.json",
        help="JSON output path for the comparison report",
    )
    return parser.parse_args()


def run_command(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    start = time.time()
    start_utc = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    end = time.time()
    end_utc = datetime.now(timezone.utc).isoformat()
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    output = "\n".join(part for part in [stdout, stderr] if part)

    return {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "output": output,
        "duration_seconds": round(end - start, 3),
        "start_time_utc": start_utc,
        "end_time_utc": end_utc,
    }


def parse_pytest_counts(output: str) -> dict[str, int]:
    def extract(pattern: str) -> int:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    passed = extract(r"(\d+)\s+passed")
    failed = extract(r"(\d+)\s+failed")
    errors = extract(r"(\d+)\s+errors?")
    skipped = extract(r"(\d+)\s+skipped")
    xfailed = extract(r"(\d+)\s+xfailed")
    xpassed = extract(r"(\d+)\s+xpassed")
    total = passed + failed + errors + skipped + xfailed + xpassed

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "total": total,
    }


def find_traditional_test_targets(repo_root: Path) -> list[str]:
    tests_dir = repo_root / "tests"
    candidates = sorted(path for path in tests_dir.glob("test_*.py") if path.is_file())
    # Traditional baseline should not rely on dynamically generated tests.
    filtered = [path for path in candidates if path.name != "test_generated.py"]

    if filtered:
        return [path.relative_to(repo_root).as_posix() for path in filtered]

    return [path.relative_to(repo_root).as_posix() for path in candidates]


def coverage_available(repo_root: Path) -> bool:
    probe = run_command([sys.executable, "-m", "coverage", "--version"], cwd=repo_root)
    return probe["return_code"] == 0


def run_coverage_for_targets(
    repo_root: Path,
    targets: list[str],
    source_module: str,
    tag: str,
) -> dict[str, Any] | None:
    if not targets:
        return None

    coverage_file = repo_root / "reports" / f".coverage.{tag}"
    coverage_json = repo_root / "reports" / f"coverage_{tag}.json"
    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(coverage_file)

    run_result = run_command(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--source={source_module}",
            "-m",
            "pytest",
            "-q",
            *targets,
        ],
        cwd=repo_root,
        env=env,
    )

    json_result = run_command(
        [sys.executable, "-m", "coverage", "json", "-o", str(coverage_json)],
        cwd=repo_root,
        env=env,
    )

    if json_result["return_code"] != 0 or not coverage_json.exists():
        return {
            "ran": False,
            "reason": "coverage-json-failed",
            "run": run_result,
            "json": json_result,
        }

    try:
        payload = json.loads(coverage_json.read_text(encoding="utf-8"))
    except Exception:
        return {
            "ran": False,
            "reason": "coverage-json-parse-failed",
            "run": run_result,
            "json": json_result,
        }

    totals = payload.get("totals", {})
    return {
        "ran": True,
        "percent_covered": round(float(totals.get("percent_covered", 0.0)), 2),
        "covered_lines": int(totals.get("covered_lines", 0)),
        "num_statements": int(totals.get("num_statements", 0)),
        "missing_lines": int(totals.get("missing_lines", 0)),
        "excluded_lines": int(totals.get("excluded_lines", 0)),
        "run": run_result,
        "json": json_result,
        "json_path": coverage_json.relative_to(repo_root).as_posix(),
    }


def safe_mean(values: list[float]) -> float:
    clean = [value for value in values if value is not None]
    if not clean:
        return 0.0
    return round(float(mean(clean)), 3)


def load_report(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_agentic_iteration(repo_root: Path, args: argparse.Namespace, run_index: int) -> dict[str, Any]:
    run_report_path = repo_root / "reports" / f"agentic_run_{run_index}.json"
    command = [
        sys.executable,
        "main.py",
        "--source",
        args.source,
        "--test-output",
        "tests/test_generated.py",
        "--report-output",
        str(run_report_path),
        "--max-heal-attempts",
        str(args.max_heal_attempts),
        "--model",
        args.model,
        "--predictive-test-selection",
        "--base-ref",
        args.base_ref,
    ]

    execution = run_command(command, cwd=repo_root)
    report = load_report(run_report_path)
    parsed_counts = parse_pytest_counts(execution["output"])

    selected_tests = report.get("predictive_selection", {}).get("selected_tests", [])
    if not isinstance(selected_tests, list):
        selected_tests = []

    source_module = Path(args.source).stem
    coverage = run_coverage_for_targets(
        repo_root=repo_root,
        targets=[str(item) for item in selected_tests if isinstance(item, str)],
        source_module=source_module,
        tag=f"agentic_{run_index}",
    )

    return {
        "strategy": "agentic",
        "run": run_index,
        "passed": execution["passed"],
        "return_code": execution["return_code"],
        "duration_seconds": execution["duration_seconds"],
        "tests_total": parsed_counts["total"],
        "tests_passed": parsed_counts["passed"],
        "tests_failed": parsed_counts["failed"] + parsed_counts["errors"],
        "defects_detected": parsed_counts["failed"] + parsed_counts["errors"],
        "selected_tests_count": len(selected_tests),
        "heal_attempts": int(report.get("heal_attempts", 0) or 0),
        "pipeline_duration_seconds": report.get("pipeline_duration_seconds"),
        "coverage": coverage,
        "report_path": run_report_path.relative_to(repo_root).as_posix(),
        "execution": execution,
    }


def run_traditional_iteration(
    repo_root: Path,
    args: argparse.Namespace,
    run_index: int,
    test_targets: list[str],
) -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q", *test_targets]
    execution = run_command(command, cwd=repo_root)
    parsed_counts = parse_pytest_counts(execution["output"])

    source_module = Path(args.source).stem
    coverage = run_coverage_for_targets(
        repo_root=repo_root,
        targets=test_targets,
        source_module=source_module,
        tag=f"traditional_{run_index}",
    )

    return {
        "strategy": "traditional",
        "run": run_index,
        "passed": execution["passed"],
        "return_code": execution["return_code"],
        "duration_seconds": execution["duration_seconds"],
        "tests_total": parsed_counts["total"],
        "tests_passed": parsed_counts["passed"],
        "tests_failed": parsed_counts["failed"] + parsed_counts["errors"],
        "defects_detected": parsed_counts["failed"] + parsed_counts["errors"],
        "selected_tests_count": len(test_targets),
        "heal_attempts": 0,
        "pipeline_duration_seconds": None,
        "coverage": coverage,
        "report_path": None,
        "execution": execution,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, list[dict[str, Any]]] = {"agentic": [], "traditional": []}
    for row in results:
        by_strategy.setdefault(row["strategy"], []).append(row)

    summary: dict[str, Any] = {}
    for strategy, rows in by_strategy.items():
        coverage_values = []
        pipeline_durations = []
        for row in rows:
            coverage = row.get("coverage") or {}
            if isinstance(coverage, dict) and coverage.get("ran"):
                coverage_values.append(float(coverage.get("percent_covered", 0.0)))
            if row.get("pipeline_duration_seconds") is not None:
                pipeline_durations.append(float(row.get("pipeline_duration_seconds", 0.0)))

        total_tests_total = sum(int(row.get("tests_total", 0) or 0) for row in rows)
        total_tests_passed = sum(int(row.get("tests_passed", 0) or 0) for row in rows)
        total_tests_failed = sum(int(row.get("tests_failed", 0) or 0) for row in rows)
        total_defects_detected = sum(int(row.get("defects_detected", 0) or 0) for row in rows)
        total_selected_tests = sum(int(row.get("selected_tests_count", 0) or 0) for row in rows)
        total_heal_attempts = sum(int(row.get("heal_attempts", 0) or 0) for row in rows)
        per_test_durations = []
        for row in rows:
            duration_seconds = float(row.get("duration_seconds", 0.0))
            tests_total = int(row.get("tests_total", 0) or 0)
            per_test_durations.append(duration_seconds / tests_total if tests_total else duration_seconds)

        summary[strategy] = {
            "runs": len(rows),
            "pass_rate_percent": round(
                (sum(1 for row in rows if row.get("passed")) / len(rows) * 100.0) if rows else 0.0,
                2,
            ),
            "total_tests_total": total_tests_total,
            "total_tests_passed": total_tests_passed,
            "total_tests_failed": total_tests_failed,
            "total_defects_detected": total_defects_detected,
            "total_selected_tests": total_selected_tests,
            "total_heal_attempts": total_heal_attempts,
            "avg_duration_seconds": safe_mean([float(row.get("duration_seconds", 0.0)) for row in rows]),
            "avg_duration_per_test_seconds": safe_mean(per_test_durations),
            "avg_pipeline_duration_seconds": safe_mean(pipeline_durations),
            "avg_tests_total": safe_mean([float(row.get("tests_total", 0.0)) for row in rows]),
            "avg_tests_passed": safe_mean([float(row.get("tests_passed", 0.0)) for row in rows]),
            "avg_tests_failed": safe_mean([float(row.get("tests_failed", 0.0)) for row in rows]),
            "avg_defects_detected": safe_mean([float(row.get("defects_detected", 0.0)) for row in rows]),
            "avg_selected_tests": safe_mean([float(row.get("selected_tests_count", 0.0)) for row in rows]),
            "avg_heal_attempts": safe_mean([float(row.get("heal_attempts", 0.0)) for row in rows]),
            "avg_coverage_percent": safe_mean(coverage_values),
        }

    agentic = summary.get("agentic", {})
    traditional = summary.get("traditional", {})
    summary["delta_agentic_minus_traditional"] = {
        "duration_seconds": round(
            float(agentic.get("avg_duration_seconds", 0.0))
            - float(traditional.get("avg_duration_seconds", 0.0)),
            3,
        ),
        "duration_per_test_seconds": round(
            float(agentic.get("avg_duration_per_test_seconds", 0.0))
            - float(traditional.get("avg_duration_per_test_seconds", 0.0)),
            3,
        ),
        "coverage_percent": round(
            float(agentic.get("avg_coverage_percent", 0.0))
            - float(traditional.get("avg_coverage_percent", 0.0)),
            3,
        ),
        "defects_detected": round(
            float(agentic.get("avg_defects_detected", 0.0))
            - float(traditional.get("avg_defects_detected", 0.0)),
            3,
        ),
        "selected_tests": round(
            float(agentic.get("avg_selected_tests", 0.0))
            - float(traditional.get("avg_selected_tests", 0.0)),
            3,
        ),
    }

    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "strategy",
                "run",
                "passed",
                "return_code",
                "duration_seconds",
                "tests_total",
                "tests_passed",
                "tests_failed",
                "defects_detected",
                "selected_tests_count",
                "heal_attempts",
                "coverage_percent",
                "report_path",
            ]
        )
        for row in rows:
            coverage = row.get("coverage") or {}
            coverage_percent = coverage.get("percent_covered") if isinstance(coverage, dict) else None
            writer.writerow(
                [
                    row.get("strategy"),
                    row.get("run"),
                    row.get("passed"),
                    row.get("return_code"),
                    row.get("duration_seconds"),
                    row.get("tests_total"),
                    row.get("tests_passed"),
                    row.get("tests_failed"),
                    row.get("defects_detected"),
                    row.get("selected_tests_count"),
                    row.get("heal_attempts"),
                    coverage_percent,
                    row.get("report_path") or "",
                ]
            )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = payload.get("runs", [])
    summary = payload.get("summary", {})
    delta = summary.get("delta_agentic_minus_traditional", {})

    def fmt_number(value: Any, digits: int = 2) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "0"

        text = f"{number:.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def metric_row(label: str, agentic_key: str, traditional_key: str, delta_key: str, better: str) -> str:
        agentic_value = summary.get("agentic", {}).get(agentic_key, 0.0)
        traditional_value = summary.get("traditional", {}).get(traditional_key, 0.0)
        delta_value = delta.get(delta_key, 0.0)
        if better == "Higher":
            if agentic_value > traditional_value:
                winner = "Agentic"
            elif traditional_value > agentic_value:
                winner = "Traditional"
            else:
                winner = "Tie"
        else:
            if agentic_value < traditional_value:
                winner = "Agentic"
            elif traditional_value < agentic_value:
                winner = "Traditional"
            else:
                winner = "Tie"
        return "| " + " | ".join(
            [
                label,
                fmt_number(agentic_value),
                fmt_number(traditional_value),
                fmt_number(delta_value, 3),
                better,
                winner,
            ]
        ) + " |"

    def total_row(strategy: str) -> str:
        item = summary.get(strategy, {})
        return "| " + " | ".join(
            [
                strategy,
                str(item.get("total_tests_total", 0)),
                str(item.get("total_tests_passed", 0)),
                str(item.get("total_tests_failed", 0)),
            ]
        ) + " |"

    metric_specs = [
        ("pass_rate_percent", "pass_rate_percent", "Higher", 0.18),
        ("avg_duration_per_test_seconds", "avg_duration_per_test_seconds", "Lower", 0.14),
        ("avg_pipeline_duration_seconds", "avg_pipeline_duration_seconds", "Lower", 0.08),
        ("avg_coverage_percent", "avg_coverage_percent", "Higher", 0.16),
        ("avg_defects_detected", "avg_defects_detected", "Higher", 0.12),
        ("avg_selected_tests", "avg_selected_tests", "Lower", 0.08),
        ("avg_heal_attempts", "avg_heal_attempts", "Lower", 0.08),
        ("avg_tests_total", "avg_tests_total", "Higher", 0.08),
        ("avg_tests_passed", "avg_tests_passed", "Higher", 0.08),
        ("avg_tests_failed", "avg_tests_failed", "Lower", 0.08),
    ]

    def weighted_score(strategy: str) -> float:
        score = 0.0
        for agentic_key, traditional_key, direction, weight in metric_specs:
            agentic_value = float(summary.get("agentic", {}).get(agentic_key, 0.0))
            traditional_value = float(summary.get("traditional", {}).get(traditional_key, 0.0))

            if agentic_value == traditional_value:
                contribution = 0.5
            elif direction == "Higher":
                contribution = 1.0 if (strategy == "agentic" and agentic_value > traditional_value) or (
                    strategy == "traditional" and traditional_value > agentic_value
                ) else 0.0
            else:
                contribution = 1.0 if (strategy == "agentic" and agentic_value < traditional_value) or (
                    strategy == "traditional" and traditional_value < agentic_value
                ) else 0.0

            score += weight * contribution

        return round(score * 100.0, 2)

    agentic_score = weighted_score("agentic")
    traditional_score = weighted_score("traditional")
    if agentic_score > traditional_score:
        overall_winner = "Agentic"
    elif traditional_score > agentic_score:
        overall_winner = "Traditional"
    else:
        overall_winner = "Tie"

    lines = [
        "# Final Comparison Report",
        "",
        "> **Legacy diagnostic only:** failed pytest cases are not unique defects. Do not cite this report as thesis evidence; use `run_research_experiment.py`.",
        "",
        f"Generated: {payload.get('generated_at_utc', '')}",
        "",
        "## Benchmark Totals",
        "",
        "| Strategy | Test cases | Tests passed | Tests failed |",
        "|---|---:|---:|---:|",
        total_row("agentic"),
        total_row("traditional"),
        "",
        "## Comparison Matrix",
        "",
        "| Metric | Agentic | Traditional | Delta (A - T) | Better | Winner |",
        "|---|---:|---:|---:|---|---|",
        metric_row("Pass rate (%)", "pass_rate_percent", "pass_rate_percent", "pass_rate_percent", "Higher"),
        metric_row("Avg execution time per test (s)", "avg_duration_per_test_seconds", "avg_duration_per_test_seconds", "duration_per_test_seconds", "Lower"),
        metric_row("Avg pipeline duration (s)", "avg_pipeline_duration_seconds", "avg_pipeline_duration_seconds", "duration_seconds", "Lower"),
        metric_row("Avg coverage (%)", "avg_coverage_percent", "avg_coverage_percent", "coverage_percent", "Higher"),
        metric_row("Avg defects detected", "avg_defects_detected", "avg_defects_detected", "defects_detected", "Higher"),
        metric_row("Avg tests selected", "avg_selected_tests", "avg_selected_tests", "selected_tests", "Lower"),
        metric_row("Avg heal attempts", "avg_heal_attempts", "avg_heal_attempts", "heal_attempts", "Lower"),
        metric_row("Avg test cases", "avg_tests_total", "avg_tests_total", "selected_tests", "Higher"),
        metric_row("Avg tests passed", "avg_tests_passed", "avg_tests_passed", "selected_tests", "Higher"),
        metric_row("Avg tests failed", "avg_tests_failed", "avg_tests_failed", "selected_tests", "Lower"),
        "",
        f"**Weighted score**: Agentic {fmt_number(agentic_score, 2)} vs Traditional {fmt_number(traditional_score, 2)}. Overall winner: {overall_winner}.",
        "",
        "## Summary",
        "",
        "| Strategy | Runs | Pass Rate % | Test Cases | Passed | Failed | Avg Execution Time / Test (s) | Avg Pipeline Duration (s) | Avg Coverage % | Avg Defects Detected | Avg Tests Selected | Avg Heal Attempts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for strategy in ["agentic", "traditional"]:
        item = summary.get(strategy, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    strategy,
                    str(item.get("runs", 0)),
                    fmt_number(item.get("pass_rate_percent", 0.0)),
                    str(item.get("total_tests_total", 0)),
                    str(item.get("total_tests_passed", 0)),
                    str(item.get("total_tests_failed", 0)),
                    fmt_number(item.get("avg_duration_per_test_seconds", 0.0)),
                    fmt_number(item.get("avg_pipeline_duration_seconds", 0.0)),
                    fmt_number(item.get("avg_coverage_percent", 0.0)),
                    fmt_number(item.get("avg_defects_detected", 0.0)),
                    fmt_number(item.get("avg_selected_tests", 0.0)),
                    fmt_number(item.get("avg_heal_attempts", 0.0)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Delta (Agentic - Traditional)",
            "",
            f"- Execution time per test seconds: {fmt_number(delta.get('duration_per_test_seconds', 0.0), 3)}",
            f"- Coverage percent: {fmt_number(delta.get('coverage_percent', 0.0), 3)}",
            f"- Defects detected: {fmt_number(delta.get('defects_detected', 0.0), 3)}",
            f"- Selected tests: {fmt_number(delta.get('selected_tests', 0.0), 3)}",
            f"- Heal attempts: {fmt_number(delta.get('heal_attempts', 0.0), 3)}",
            "",
            "## Per-Run Details",
            "",
            "| Strategy | Run | Passed | Duration (s) | Tests (passed/total) | Defects | Selected Tests | Coverage % |",
            "|---|---:|---|---:|---|---:|---:|---:|",
        ]
    )

    for row in rows:
        coverage = row.get("coverage") or {}
        coverage_percent = coverage.get("percent_covered", 0.0) if isinstance(coverage, dict) else 0.0
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("strategy", "")),
                    str(row.get("run", "")),
                    str(row.get("passed", "")),
                    fmt_number(row.get("duration_seconds", ""), 3),
                    f"{row.get('tests_passed', 0)}/{row.get('tests_total', 0)}",
                    str(row.get("defects_detected", "")),
                    str(row.get("selected_tests_count", "")),
                    fmt_number(coverage_percent, 2),
                ]
            )
            + " |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    print(
        "WARNING: legacy proxy metrics only; use scripts/run_research_experiment.py for thesis evidence.",
        file=sys.stderr,
    )
    repo_root = Path(__file__).resolve().parent.parent
    report_output = repo_root / args.report_output
    report_output.parent.mkdir(parents=True, exist_ok=True)

    traditional_targets = find_traditional_test_targets(repo_root)
    if not traditional_targets:
        print("No tests found for traditional baseline in tests/.")
        return 1

    if not coverage_available(repo_root):
        print("Coverage is not installed. Install 'coverage' to include line coverage metrics.")

    results: list[dict[str, Any]] = []

    for run_index in range(1, args.runs + 1):
        print(f"[agentic] run {run_index}/{args.runs}")
        results.append(run_agentic_iteration(repo_root, args, run_index))

    for run_index in range(1, args.runs + 1):
        print(f"[traditional] run {run_index}/{args.runs}")
        results.append(run_traditional_iteration(repo_root, args, run_index, traditional_targets))

    payload = {
        "research_evidence_valid": False,
        "research_evidence_warning": "Legacy proxy metrics; pytest failures are not unique defects.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "runs_per_strategy": args.runs,
        "traditional_test_targets": traditional_targets,
        "runs": results,
        "summary": summarize(results),
    }

    report_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_output = report_output.with_suffix(".csv")
    md_output = report_output.with_suffix(".md")
    write_csv(csv_output, results)
    write_markdown(md_output, payload)

    print(f"Wrote {report_output}")
    print(f"Wrote {csv_output}")
    print(f"Wrote {md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
