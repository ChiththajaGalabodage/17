"""Prototype harness to run LLM-based test generation, validation, execution, and healing.

Usage:
  python scripts/llm_prototype_harness.py --source target_code.py --output tests/generated_tests.py

This script is intentionally lightweight: it uses the existing modules in `src/`.
If an LLM API key is available (GEMINI_API_KEY), it will attempt to use the live generator;
otherwise it falls back to deterministic generators/healers included in the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import run_pipeline


class PrototypePipelineFailure(RuntimeError):
    """Raised when the production pipeline returns a nonzero exit code."""

    def __init__(self, exit_code: int, report: dict[str, Any]) -> None:
        self.exit_code = int(exit_code)
        self.report = report
        super().__init__(f"Pipeline exited with status {self.exit_code}")


def load_local_env(env_path: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs from a local .env file if present.

    Existing process environment variables are preserved.
    """
    candidate = env_path or (ROOT / ".env")
    if not candidate.exists():
        return

    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def run_prototype(
    source: str,
    output: str | None = None,
    report_output: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around the fail-closed production pipeline."""
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    output_path = Path(output or f"tests/generated_tests_{source_path.stem}.py")
    report_path = (
        Path(report_output)
        if report_output
        else Path("reports") / f"prototype_run_{source_path.stem}.json"
    )
    args = SimpleNamespace(
        source=str(source_path),
        test_output=str(output_path),
        report_output=str(report_path),
        max_heal_attempts=2,
        model="gemini-2.5-flash",
        temperature=0.2,
        seed=4885,
        offline=False,
        watch=False,
        watch_interval=1.0,
        predictive_test_selection=False,
        selection_mode="hybrid",
        base_ref="HEAD~1",
        stability_runs=3,
        minimum_target_coverage=50.0,
        test_timeout=60.0,
    )
    pipeline_exit_code = int(run_pipeline(args))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Preserve historical keys for callers while retaining the full new report.
    report["source"] = str(source_path)
    report["output_test"] = str(output_path)
    report["run1"] = report.get("test_run", {})
    report["healer_applied"] = bool(report.get("heal_attempts", 0))
    report["pipeline_exit_code"] = pipeline_exit_code
    if pipeline_exit_code != 0:
        raise PrototypePipelineFailure(pipeline_exit_code, report)
    return report


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="target_code.py", help="Path to the source module to generate tests for")
    p.add_argument("--output", help="Output test file path (optional)")
    p.add_argument("--report-output", help="Prototype report path (optional)")
    return p.parse_args()


def main() -> None:
    load_local_env()
    args = _parse_args()
    try:
        report = run_prototype(args.source, args.output, args.report_output)
    except PrototypePipelineFailure as error:
        print("Prototype run failed:", error)
        raise SystemExit(error.exit_code) from error
    except Exception as e:
        print("Prototype run failed:", e)
        raise


if __name__ == "__main__":
    main()
