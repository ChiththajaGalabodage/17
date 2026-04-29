import argparse
import sys
from pathlib import Path

from src.analyzer import analyze_code
from src.generator import GeminiTestGenerator
from src.healer import heal_test_bundle
from src.output_format import normalize_test_code
from src.pipeline_tracker import PipelineTracker
from src.reporter import build_report, write_report
from src.runner import run_pytest, run_pytest_targets
from src.test_select_agent import TestSelectAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Test Generator pipeline")
    parser.add_argument("--source", default="target_code.py", help="Path to target Python file")
    parser.add_argument("--test-output", default="tests/test_generated.py", help="Generated pytest path")
    parser.add_argument("--report-output", default="reports/report.json", help="JSON report path")
    parser.add_argument("--max-heal-attempts", type=int, default=2, help="Max self-heal retries")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument(
        "--predictive-test-selection",
        action="store_true",
        help="Select impacted tests from git changes and run only those tests",
    )
    parser.add_argument(
        "--base-ref",
        default="HEAD~1",
        help="Base git ref used to detect changed files for predictive selection",
    )
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> int:
    tracker = PipelineTracker()
    tracker.record("pipeline", "started", "Pipeline run started", source=args.source)

    source_path = Path(args.source)
    if not source_path.exists():
        tracker.record("pipeline", "failed", "Source file not found", source=args.source)
        print(f"Source file not found: {source_path}")
        return 1

    print("[1/5] Analyzing source code...")
    tracker.record("analysis", "running", "Analyzing source code")
    analysis = analyze_code(str(source_path))
    tracker.record(
        "analysis",
        "completed",
        "Source analysis completed",
        function_count=analysis.get("function_count", 0),
        class_count=analysis.get("class_count", 0),
    )

    print("[2/5] Generating tests...")
    tracker.record("generation", "running", "Generating tests")
    generator = GeminiTestGenerator(model=args.model)
    generation_bundle = generator.generate(str(source_path), analysis)
    test_code = normalize_test_code(generation_bundle["test_code"], source_path)
    generation_explanation = generation_bundle.get("explanation", [])

    test_output_path = Path(args.test_output)
    test_output_path.parent.mkdir(parents=True, exist_ok=True)
    test_output_path.write_text(test_code, encoding="utf-8")
    tracker.record(
        "generation",
        "completed",
        "Test file written",
        test_file=str(test_output_path),
        explanation_lines=len(generation_explanation),
    )

    print("[3/5] Running tests...")
    tracker.record("test_run", "running", "Executing initial test run", test_file=str(test_output_path))
    test_result = run_pytest(str(test_output_path))
    tracker.record(
        "test_run",
        "completed",
        "Initial test run completed",
        passed=test_result["passed"],
        return_code=test_result["return_code"],
    )

    predictive_selection: dict[str, object] = {
        "enabled": args.predictive_test_selection,
        "base_ref": args.base_ref,
        "changed_files": [],
        "selected_tests": [str(test_output_path).replace("\\", "/")],
    }

    if args.predictive_test_selection:
        selector = TestSelectAgent(repo_root=".")
        changed_files = selector.get_changed_files(base_ref=args.base_ref)
        selected_tests = selector.select_tests(changed_files)

        generated_test = test_output_path.as_posix()
        if generated_test not in selected_tests:
            selected_tests.append(generated_test)

        selected_tests = sorted(set(selected_tests))
        predictive_selection["changed_files"] = changed_files
        predictive_selection["selected_tests"] = selected_tests
        print(f"Predictive selection picked {len(selected_tests)} test file(s).")
        for selected in selected_tests:
            print(f" - {selected}")
        tracker.record(
            "selection",
            "completed",
            "Predictive selection completed",
            changed_files=changed_files,
            selected_tests=selected_tests,
        )
        tracker.record("test_run", "running", "Executing selected tests", selected_tests=selected_tests)
        test_result = run_pytest_targets(selected_tests)
        tracker.record(
            "test_run",
            "completed",
            "Selected test run completed",
            passed=test_result["passed"],
            return_code=test_result["return_code"],
        )

    heal_attempts = 0
    heal_history: list[dict[str, object]] = []
    while not test_result["passed"] and heal_attempts < args.max_heal_attempts:
        heal_attempts += 1
        print(f"[4/5] Self-heal attempt {heal_attempts}/{args.max_heal_attempts}...")
        tracker.record("healing", "running", "Self-heal attempt started", attempt=heal_attempts)
        heal_bundle = heal_test_bundle(
            current_test_code=test_code,
            test_output=test_result["output"],
            analysis=analysis,
            ai_generator=generator,
        )
        test_code = normalize_test_code(heal_bundle["test_code"], source_path)
        if heal_bundle.get("explanation"):
            generation_explanation = heal_bundle["explanation"]
        test_output_path.write_text(test_code, encoding="utf-8")
        heal_history.append({
            "attempt": heal_attempts,
            "test_output": test_result["output"],
            "result": "retry",
        })
        test_result = run_pytest(str(test_output_path))
        tracker.record(
            "healing",
            "completed",
            "Self-heal attempt completed",
            attempt=heal_attempts,
            passed=test_result["passed"],
            return_code=test_result["return_code"],
        )

    print("[5/5] Writing report...")
    tracker.record("report", "running", "Writing report")
    status = "PASSED" if test_result["passed"] else "FAILED"
    tracker.record("pipeline", status.lower(), f"Pipeline finished: {status}")
    report = build_report(
        analysis=analysis,
        test_run=test_result,
        heal_attempts=heal_attempts,
        test_file=str(test_output_path),
        pipeline_events=tracker.snapshot(),
        predictive_selection=predictive_selection,
        heal_history=heal_history,
        generation_explanation=generation_explanation,
    )
    write_report(report, args.report_output)

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