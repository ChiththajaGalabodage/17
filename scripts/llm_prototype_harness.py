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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyzer import analyze_code
from src.generator import GeminiTestGenerator
from src.output_format import normalize_test_code
from src.validator import validate_generated_test_code, build_smoke_test_code
from src.runner import run_pytest
from src.healer import heal_test_code


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
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    output_path = Path(output or f"tests/generated_tests_{source_path.stem}.py")

    print(f"Analyzing {source}")
    analysis = analyze_code(str(source_path))

    print("Initializing generator (live model if available)")
    generator = GeminiTestGenerator(api_key=os.getenv("GEMINI_API_KEY"))

    print("Requesting test generation...")
    bundle = generator.generate(str(source_path), analysis)
    raw_code = bundle.get("test_code", "")

    print("Normalizing generated code for the target module")
    normalized = normalize_test_code(raw_code, source_path)

    print("Validating generated test code before execution")
    validation = validate_generated_test_code(normalized, source_path, analysis)
    if not validation.get("passed", False):
        print("Validation failed:", validation.get("issues", []))
        print("Falling back to smoke test to avoid pipeline crash")
        normalized = build_smoke_test_code(source_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(normalized, encoding="utf-8")
    print(f"Wrote generated tests to {output_path}")

    print("Running pytest on generated tests (first attempt)")
    run1 = run_pytest(str(output_path))
    print(f"Run1: passed={run1['passed']} return_code={run1['return_code']} duration={run1['duration_seconds']}s")

    report: dict[str, Any] = {
        "source": str(source_path),
        "output_test": str(output_path),
        "analysis": analysis,
        "generation_explanation": bundle.get("explanation", []),
        "validation": validation,
        "run1": run1,
    }

    if not run1.get("passed"):
        print("Initial run failed. Attempting automated healing...")
        healed = heal_test_code(normalized, run1.get("output", ""), analysis, generator if generator.can_use_ai else None)
        # heal_test_code returns a string of code
        healed_norm = normalize_test_code(healed, source_path)
        output_path.write_text(healed_norm, encoding="utf-8")
        print(f"Wrote healed tests to {output_path}")

        run2 = run_pytest(str(output_path))
        print(f"Run2: passed={run2['passed']} return_code={run2['return_code']} duration={run2['duration_seconds']}s")
        report["healer_applied"] = True
        report["run2"] = run2
    else:
        report["healer_applied"] = False

    report_path = Path(report_output) if report_output else Path("reports") / f"prototype_run_{source_path.stem}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Prototype report written to {report_path}")

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
    except Exception as e:
        print("Prototype run failed:", e)
        raise


if __name__ == "__main__":
    main()
