import argparse
import re
import sys
from pathlib import Path

from src.analyzer import analyze_code
from src.generator import GeminiTestGenerator
from src.healer import heal_test_code
from src.reporter import build_report, write_report
from src.runner import run_pytest


def normalize_test_code(test_code: str, source_path: Path) -> str:
    """Normalize model output into executable pytest code."""
    cleaned = test_code.strip()
    if cleaned.startswith("```python"):
        cleaned = cleaned[len("```python") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    module_name = source_path.stem
    body_lines: list[str] = []

    for raw_line in cleaned.splitlines():
        line = raw_line.rstrip()

        # Remove known import fragments even when the model glued them onto code.
        line = re.sub(r"import\s+pytest\b.*", "", line)
        line = re.sub(rf"from\s+{re.escape(module_name)}\s+import\b.*", "", line)
        line = re.sub(rf"import\s+{re.escape(module_name)}\b.*", "", line)

        if line.strip():
            body_lines.append(line.rstrip())

    normalized_lines = ["import pytest", f"from {module_name} import *", ""]
    normalized_lines.extend(body_lines)

    return "\n".join(normalized_lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Test Generator pipeline")
    parser.add_argument("--source", default="target_code.py", help="Path to target Python file")
    parser.add_argument("--test-output", default="tests/test_generated.py", help="Generated pytest path")
    parser.add_argument("--report-output", default="reports/report.json", help="JSON report path")
    parser.add_argument("--max-heal-attempts", type=int, default=2, help="Max self-heal retries")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Source file not found: {source_path}")
        return 1

    print("[1/5] Analyzing source code...")
    analysis = analyze_code(str(source_path))

    print("[2/5] Generating tests...")
    generator = GeminiTestGenerator(model=args.model)
    test_code = generator.generate(str(source_path), analysis)
    test_code = normalize_test_code(test_code, source_path)

    test_output_path = Path(args.test_output)
    test_output_path.parent.mkdir(parents=True, exist_ok=True)
    test_output_path.write_text(test_code, encoding="utf-8")

    print("[3/5] Running tests...")
    test_result = run_pytest(str(test_output_path))

    heal_attempts = 0
    while not test_result["passed"] and heal_attempts < args.max_heal_attempts:
        heal_attempts += 1
        print(f"[4/5] Self-heal attempt {heal_attempts}/{args.max_heal_attempts}...")
        test_code = heal_test_code(
            current_test_code=test_code,
            test_output=test_result["output"],
            analysis=analysis,
            ai_generator=generator,
        )
        test_code = normalize_test_code(test_code, source_path)
        test_output_path.write_text(test_code, encoding="utf-8")
        test_result = run_pytest(str(test_output_path))

    print("[5/5] Writing report...")
    report = build_report(
        analysis=analysis,
        test_run=test_result,
        heal_attempts=heal_attempts,
        test_file=str(test_output_path),
    )
    write_report(report, args.report_output)

    status = "PASSED" if test_result["passed"] else "FAILED"
    print(f"Pipeline finished: {status}")
    print(f"Generated tests: {test_output_path}")
    print(f"Report: {args.report_output}")
    if test_result["output"]:
        print("\nPytest output:\n")
        print(test_result["output"])

    return 0 if test_result["passed"] else 1


if __name__ == "__main__":
    cli_args = parse_args()
    sys.exit(run_pipeline(cli_args))