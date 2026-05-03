import json
import re
from pathlib import Path


def parse_test_output(output: str) -> tuple[int, int]:
    """Return (passed_count, total_count) parsed from pytest output text.

    Falls back to (1,1) when output is missing but the run reports passed=True.
    """
    if not output:
        return (0, 0)

    passed = 0
    failed = 0
    error = 0
    skipped = 0

    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))

    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))

    m = re.search(r"(\d+)\s+error", output)
    if m:
        error = int(m.group(1))

    m = re.search(r"(\d+)\s+skipped", output)
    if m:
        skipped = int(m.group(1))

    total = passed + failed + error + skipped
    if total == 0 and passed > 0:
        total = passed

    return passed, total


def aggregate_reports(reports_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        ts = data.get("timestamp_utc", "")
        test_run = data.get("test_run", {})
        passed_flag = bool(test_run.get("passed", False))
        return_code = int(test_run.get("return_code", 1))
        output = str(test_run.get("output", ""))

        passed_count, total_count = parse_test_output(output)
        if total_count == 0:
            # best-effort fallback
            total_count = passed_count or (1 if passed_flag else 0)

        accuracy = (passed_count / total_count * 100.0) if total_count else 0.0

        rows.append(
            {
                "timestamp_utc": ts,
                "passed": passed_flag,
                "return_code": return_code,
                "tests_passed": passed_count,
                "tests_total": total_count,
                "accuracy_percent": round(accuracy, 2),
                "report_file": path.name,
            }
        )

    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    header = [
        "timestamp_utc",
        "passed",
        "return_code",
        "tests_passed",
        "tests_total",
        "accuracy_percent",
        "report_file",
    ]
    lines = [",".join(header)]
    for r in rows:
        lines.append(
            ",".join(
                [
                    r["timestamp_utc"],
                    str(r["passed"]),
                    str(r["return_code"]),
                    str(r["tests_passed"]),
                    str(r["tests_total"]),
                    str(r["accuracy_percent"]),
                    r["report_file"],
                ]
            )
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary_md(rows: list[dict], out_path: Path) -> None:
    total_runs = len(rows)
    total_tests = sum(r["tests_total"] for r in rows)
    total_passed = sum(r["tests_passed"] for r in rows)
    overall_accuracy = round((total_passed / total_tests * 100.0) if total_tests else 0.0, 2)

    lines = [
        "# Project Status Report",
        "",
        f"Generated: {rows[0]['timestamp_utc'] if rows else ''}",
        "",
        "Summary:",
        "",
        "- Project: AI Test Generator pipeline",
        "- Source analyzed: `target_code.py`",
        f"- Runs considered: {total_runs}",
        "",
        "Test run aggregate:",
        "",
        f"- Total tests passed across runs: {total_passed}",
        f"- Total tests executed across runs: {total_tests}",
        f"- Overall accuracy rate: {overall_accuracy}%",
        "",
        "Per-run details:",
        "",
    ]

    for r in rows:
        lines.append(
            f"- {r['timestamp_utc']}: passed={r['passed']}, return_code={r['return_code']}, tests={r['tests_passed']}/{r['tests_total']}, accuracy={r['accuracy_percent']}% ({r['report_file']})"
        )

    lines.append("")
    lines.append("Notes:")
    lines.append("")
    lines.append("- The accuracy rate above is computed from all JSON reports in the `reports/` directory.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    repo = Path(__file__).resolve().parent.parent
    reports_dir = repo / "reports"
    out_csv = reports_dir / "project_status.csv"
    out_md = reports_dir / "project_status.md"

    rows = aggregate_reports(reports_dir)
    if not rows:
        print("No reports found in", reports_dir)
    else:
        write_csv(rows, out_csv)
        write_summary_md(rows, out_md)
        print(f"Wrote {out_csv} and {out_md}")
