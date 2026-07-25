import argparse
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.analyzer import analyze_code
from src.generator import GeminiTestGenerator
from src.healer import classify_failure, heal_test_bundle
from src.output_format import normalize_test_code
from src.pipeline_tracker import PipelineTracker
from src.reporter import build_report, write_report
from src.runner import run_pytest, run_pytest_targets, run_stability
from src.test_select_agent import TestSelectAgent
from src.validator import validate_generated_test_code


def pipeline_status(passed: bool) -> str:
    return "PASSED" if passed else "FAILED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Test Generator pipeline")
    parser.add_argument("--source", default="target_code.py", help="Path to target Python file")
    parser.add_argument("--test-output", default="tests/test_generated.py", help="Generated pytest path")
    parser.add_argument("--report-output", default="reports/report.json", help="JSON report path")
    parser.add_argument("--max-heal-attempts", type=int, default=2, help="Max self-heal retries")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Gemini sampling temperature recorded in experiment provenance",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=4885,
        help="Gemini sampling seed recorded in experiment provenance",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Disable external model calls and record deterministic-fallback provenance",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch source file for changes and rerun the pipeline automatically",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds when --watch is enabled",
    )
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
    parser.add_argument(
        "--selection-mode",
        choices=("change-impact", "hybrid", "full"),
        default="hybrid",
        help="Selection strategy used when --predictive-test-selection is enabled",
    )
    parser.add_argument(
        "--stability-runs",
        type=int,
        default=3,
        help="Repeated executions used to detect flaky outcomes",
    )
    parser.add_argument(
        "--minimum-target-coverage",
        type=float,
        default=50.0,
        help="Minimum percentage of public target callables exercised by generated tests",
    )
    parser.add_argument(
        "--test-timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds for each pytest execution",
    )
    parser.add_argument(
        "--allow-uncontained-llm-tests",
        action="store_true",
        help=(
            "Execute live LLM-generated code without an OS security sandbox; "
            "use only when this entire process already runs in a disposable container or VM"
        ),
    )
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> int:
    tracker = PipelineTracker()
    pipeline_start_ts = time.time()
    pipeline_start_utc = datetime.now(timezone.utc).isoformat()
    tracker.record("pipeline", "started", "Pipeline run started", source=args.source)

    source_path = Path(args.source)
    if not source_path.exists():
        tracker.record("pipeline", "failed", "Source file not found", source=args.source)
        print(f"Source file not found: {source_path}")
        return 1

    print("[1/6] Analyzing source code...")
    tracker.record("analysis", "running", "Analyzing source code")
    analysis = analyze_code(str(source_path))
    tracker.record(
        "analysis",
        "completed",
        "Source analysis completed",
        function_count=analysis.get("function_count", 0),
        class_count=analysis.get("class_count", 0),
    )

    print("[2/6] Generating and validating tests...")
    tracker.record("generation", "running", "Generating tests")
    generator = GeminiTestGenerator(
        api_key="" if getattr(args, "offline", False) else None,
        model=args.model,
        temperature=float(getattr(args, "temperature", 0.2)),
        seed=getattr(args, "seed", 4885),
    )
    generation_bundle = generator.generate(str(source_path), analysis)
    raw_generated_test_code = str(generation_bundle["test_code"])
    test_code = normalize_test_code(raw_generated_test_code, source_path)
    initially_normalized_test_code = test_code
    generation_explanation = generation_bundle.get("explanation", [])
    generation_provenance = generation_bundle.get("provenance", {})
    generation_repair_attempts = 0
    generation_repair_acceptances = 0

    tracker.record("validation", "running", "Validating generated tests")
    validation_result = validate_generated_test_code(
        test_code,
        source_path,
        analysis,
        minimum_target_coverage=max(float(args.minimum_target_coverage), 0.0),
    )
    live_llm_output = generation_provenance.get("backend") == "gemini"
    uncontained_execution_allowed = bool(
        getattr(args, "allow_uncontained_llm_tests", False)
    )
    execution_blocked_by_policy = (
        live_llm_output and not uncontained_execution_allowed
    )
    if execution_blocked_by_policy:
        validation_result = dict(validation_result)
        validation_result["passed"] = False
        validation_result["issues"] = [
            *validation_result.get("issues", []),
            (
                "Live LLM-generated code was not executed because the temporary-copy "
                "runner is not an OS security boundary. Run the whole framework in a "
                "disposable network-restricted container/VM and explicitly pass "
                "--allow-uncontained-llm-tests."
            ),
        ]
    validation_history: list[dict[str, object]] = [
        {"phase": "initial", "result": validation_result}
    ]
    if not validation_result["passed"]:
        tracker.record(
            "validation",
            "failed",
            "Generated tests failed validation",
            issues=validation_result["issues"],
        )
        print("Generated tests failed validation:")
        for issue in validation_result["issues"]:
            print(f" - {issue}")

        # A generation repair can correct invalid test code, but it cannot
        # override the execution-containment policy.  Without this guard, a
        # repaired live-LLM candidate could become valid and be executed even
        # though --allow-uncontained-llm-tests was not provided.
        if generator.can_use_ai and not execution_blocked_by_policy:
            generation_repair_attempts += 1
            heal_bundle = heal_test_bundle(
                current_test_code=test_code,
                test_output=(
                    "ERROR collecting generated tests\n"
                    + "\n".join(validation_result["issues"])
                ),
                analysis=analysis,
                ai_generator=generator,
            )
            candidate_code = normalize_test_code(heal_bundle["test_code"], source_path)
            candidate_validation = validate_generated_test_code(
                candidate_code,
                source_path,
                analysis,
                minimum_target_coverage=max(float(args.minimum_target_coverage), 0.0),
            )
            validation_history.append(
                {
                    "phase": "generation-repair",
                    "action": heal_bundle.get("action"),
                    "accepted": bool(candidate_validation["passed"]),
                    "before_sha256": hashlib.sha256(test_code.encode("utf-8")).hexdigest(),
                    "after_sha256": hashlib.sha256(candidate_code.encode("utf-8")).hexdigest(),
                    "result": candidate_validation,
                }
            )
            if candidate_validation["passed"]:
                generation_repair_acceptances += 1
                test_code = candidate_code
                validation_result = candidate_validation
                if heal_bundle.get("explanation"):
                    generation_explanation = heal_bundle["explanation"]
        else:
            print("Validation failed; the candidate is retained as evidence but will not be executed.")

    tracker.record(
        "validation",
        "completed" if validation_result["passed"] else "failed",
        "Generated-test validation finished",
        passed=validation_result["passed"],
        issues=validation_result["issues"],
        warnings=validation_result.get("warnings", []),
        metrics=validation_result.get("metrics", {}),
    )

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

    predictive_selection: dict[str, object] = {
        "enabled": args.predictive_test_selection,
        "mode": args.selection_mode,
        "base_ref": args.base_ref,
        "changed_files": [],
        "selected_tests": [str(test_output_path).replace("\\", "/")],
        "backend": "disabled",
        "fallback_reason": None,
    }
    heal_attempts = 0
    heal_history: list[dict[str, object]] = []
    runtime_repair_opportunities = 0
    protected_runtime_failures = 0
    final_test_targets = [test_output_path.as_posix()]
    stability: dict[str, object] = {
        "runs": 0,
        "consistent": False,
        "flaky": False,
        "reason": "Generated tests did not pass static validation",
    }

    if validation_result["passed"]:
        print("[3/6] Executing generated tests...")
        tracker.record(
            "test_run",
            "running",
            "Executing generated tests",
            test_file=str(test_output_path),
        )
        test_result = run_pytest(
            str(test_output_path),
            timeout_seconds=max(float(args.test_timeout), 0.1),
            isolated=True,
        )
        tracker.record(
            "test_run",
            "completed",
            "Generated-test execution completed",
            passed=test_result["passed"],
            return_code=test_result["return_code"],
        )

        failure_classification = classify_failure(test_result.get("output", ""))
        if not test_result["passed"]:
            if failure_classification["safe_to_heal"]:
                runtime_repair_opportunities += 1
            else:
                protected_runtime_failures += 1
        while (
            not test_result["passed"]
            and failure_classification["safe_to_heal"]
            and heal_attempts < args.max_heal_attempts
        ):
            heal_attempts += 1
            print(f"[4/6] Safe self-heal attempt {heal_attempts}/{args.max_heal_attempts}...")
            tracker.record(
                "healing", "running", "Self-heal attempt started", attempt=heal_attempts
            )
            previous_code = test_code
            previous_signature = validation_result.get("metrics", {}).get(
                "quality_signature", [0, 0, 0]
            )
            heal_bundle = heal_test_bundle(
                current_test_code=test_code,
                test_output=test_result["output"],
                analysis=analysis,
                ai_generator=generator,
            )
            candidate_code = normalize_test_code(heal_bundle["test_code"], source_path)
            candidate_validation = validate_generated_test_code(
                candidate_code,
                source_path,
                analysis,
                minimum_target_coverage=max(float(args.minimum_target_coverage), 0.0),
            )
            candidate_signature = candidate_validation.get("metrics", {}).get(
                "quality_signature", [0, 0, 0]
            )
            accepted = (
                candidate_code != previous_code
                and candidate_validation["passed"]
                and _quality_not_weaker(previous_signature, candidate_signature)
            )
            history_item: dict[str, object] = {
                "attempt": heal_attempts,
                "classification": failure_classification,
                "action": heal_bundle.get("action", "unknown"),
                "accepted": accepted,
                "validation": candidate_validation,
                "before_sha256": hashlib.sha256(previous_code.encode("utf-8")).hexdigest(),
                "after_sha256": hashlib.sha256(candidate_code.encode("utf-8")).hexdigest(),
            }
            if not accepted:
                history_item["rejection_reason"] = (
                    "Repair was unchanged, invalid, or weakened the test oracle"
                )
                heal_history.append(history_item)
                tracker.record(
                    "healing",
                    "failed",
                    "Self-heal candidate rejected",
                    attempt=heal_attempts,
                )
                break

            test_code = candidate_code
            validation_result = candidate_validation
            validation_history.append(
                {"phase": f"runtime-repair-{heal_attempts}", "result": candidate_validation}
            )
            if heal_bundle.get("explanation"):
                generation_explanation = heal_bundle["explanation"]
            test_output_path.write_text(test_code, encoding="utf-8")
            test_result = run_pytest(
                str(test_output_path),
                timeout_seconds=max(float(args.test_timeout), 0.1),
                isolated=True,
            )
            history_item["post_repair_result"] = test_result
            heal_history.append(history_item)
            tracker.record(
                "healing",
                "completed",
                "Self-heal candidate accepted and re-executed",
                attempt=heal_attempts,
                passed=test_result["passed"],
            )
            failure_classification = classify_failure(test_result.get("output", ""))
            if not test_result["passed"]:
                if failure_classification["safe_to_heal"]:
                    runtime_repair_opportunities += 1
                else:
                    protected_runtime_failures += 1

        if not test_result["passed"] and not failure_classification["safe_to_heal"]:
            tracker.record(
                "healing",
                "skipped",
                "Failure preserved as possible product-defect evidence",
                classification=failure_classification,
            )

        if args.predictive_test_selection:
            print("[5/6] Selecting impacted tests...")
            llm_selection_requested = (
                args.selection_mode == "hybrid"
                and not getattr(args, "offline", False)
            )
            selector = TestSelectAgent(
                repo_root=".",
                api_key="" if getattr(args, "offline", False) else None,
                model=args.model,
                use_llm=llm_selection_requested,
                temperature=float(getattr(args, "temperature", 0.2)),
                seed=getattr(args, "seed", 4885),
            )
            changed_files = selector.get_changed_files(base_ref=args.base_ref)
            changed_symbols = selector.get_changed_symbols(base_ref=args.base_ref)
            change_context = selector.build_change_context(changed_files, changed_symbols)
            generated_test = test_output_path.as_posix()
            if hasattr(selector, "select_with_evidence"):
                selection_evidence = selector.select_with_evidence(
                    changed_files,
                    use_llm=llm_selection_requested,
                    changed_symbols=changed_symbols,
                    change_context=change_context,
                )
                if args.selection_mode == "full":
                    selected_tests = list(selection_evidence.get("universe", []))
                    selection_evidence["backend"] = "full-suite"
                    selection_evidence["fallback"] = False
                    selection_evidence["fallback_reason"] = None
                else:
                    selected_tests = list(selection_evidence.get("selected", []))
                if generated_test not in selected_tests:
                    selected_tests.append(generated_test)
                    selection_evidence.setdefault("reasons", {})[generated_test] = [
                        "required:newly-generated-suite"
                    ]
                universe = list(selection_evidence.get("universe", []))
                if generated_test not in universe:
                    universe.append(generated_test)
                selection_evidence["universe"] = sorted(set(universe))
                selected_tests = sorted(set(selected_tests))
                selection_evidence["selected"] = selected_tests
                selection_evidence["selected_tests"] = selected_tests
                selection_evidence["requested_mode"] = args.selection_mode
                universe_count = len(selection_evidence["universe"])
                selection_evidence["selection_ratio"] = round(
                    len(selected_tests) / universe_count, 4
                ) if universe_count else 0.0
                predictive_selection.update(selection_evidence)
            else:
                selected_tests = selector.select_tests(changed_files)
                if generated_test not in selected_tests:
                    selected_tests.append(generated_test)
                selected_tests = sorted(set(selected_tests))
                predictive_selection.update(
                    {
                        "backend": "change-impact",
                        "selected_tests": selected_tests,
                        "universe": sorted(
                            str(path.relative_to(Path(".").resolve())).replace("\\", "/")
                            for path in Path("tests").resolve().glob("test_*.py")
                        ),
                    }
                )
            final_test_targets = sorted(set(selected_tests))
            predictive_selection["changed_files"] = changed_files
            predictive_selection["selected_tests"] = final_test_targets
            print(f"Selection picked {len(final_test_targets)} test file(s).")
            tracker.record(
                "selection",
                "completed",
                "Test selection completed",
                evidence=predictive_selection,
            )
            # Existing/manual test failures are never used to rewrite the
            # generated test.  This is the final semantic suite outcome.
            test_result = run_pytest_targets(
                final_test_targets,
                timeout_seconds=max(float(args.test_timeout), 0.1),
                isolated=True,
            )

        print("[6/6] Measuring repeated-run consistency...")
        stability = run_stability(
            final_test_targets,
            runs=max(int(args.stability_runs), 1),
            isolated=True,
            timeout_seconds=max(float(args.test_timeout), 0.1),
        )
    else:
        test_result = _rejected_generation_result(validation_result)

    validation_payload = dict(validation_result)
    validation_payload["history"] = validation_history
    semantic_passed = bool(
        validation_result["passed"]
        and test_result.get("passed")
        and stability.get("consistent")
        and stability.get("all_passed")
    )
    print("Writing evidence report...")
    tracker.record("report", "running", "Writing report")
    status = pipeline_status(semantic_passed)
    tracker.record(
        "pipeline",
        "passed" if semantic_passed else "failed",
        f"Pipeline finished: {status}",
        semantic_test_passed=test_result.get("passed"),
        validation_passed=validation_result.get("passed"),
        stability_consistent=stability.get("consistent"),
        stability_all_passed=stability.get("all_passed"),
        final_return_code=test_result.get("return_code"),
    )
    generation_provenance = dict(generation_provenance)
    generation_provenance["total_api_calls_including_healing"] = getattr(
        generator, "_api_calls", generation_provenance.get("api_calls", 0)
    )
    generation_provenance["api_usage_records"] = list(
        getattr(generator, "api_usage_records", [])
    )
    generation_provenance["raw_generated_test_sha256"] = hashlib.sha256(
        raw_generated_test_code.encode("utf-8")
    ).hexdigest()
    generation_provenance["initial_normalized_test_sha256"] = hashlib.sha256(
        initially_normalized_test_code.encode("utf-8")
    ).hexdigest()
    generation_provenance["normalization_changed_output"] = (
        raw_generated_test_code != initially_normalized_test_code
    )
    generation_provenance["generated_test_sha256"] = hashlib.sha256(
        test_code.encode("utf-8")
    ).hexdigest()
    generation_provenance["execution_policy"] = {
        "live_llm_output": live_llm_output,
        "uncontained_execution_allowed": uncontained_execution_allowed,
        "runner_security_boundary": False,
        "runner_isolation_level": "temporary-filesystem-copy-and-environment-scrubbing",
    }
    repair_audit = {
        "generation_validation_failed_initially": not bool(
            validation_history[0]["result"]["passed"]  # type: ignore[index]
        ),
        "generation_repair_attempts": generation_repair_attempts,
        "generation_repair_acceptances": generation_repair_acceptances,
        "runtime_repair_opportunities": runtime_repair_opportunities,
        "runtime_repair_attempts": heal_attempts,
        "runtime_repair_acceptances": sum(
            bool(item.get("accepted")) for item in heal_history
        ),
        "runtime_repair_rejections": sum(
            not bool(item.get("accepted")) for item in heal_history
        ),
        "protected_runtime_failures": protected_runtime_failures,
        "scope": "guardrail audit; not an effectiveness evaluation",
    }
    report = build_report(
        analysis=analysis,
        test_run=test_result,
        heal_attempts=heal_attempts,
        test_file=str(test_output_path),
        pipeline_events=tracker.snapshot(),
        predictive_selection=predictive_selection,
        heal_history=heal_history,
        generation_explanation=generation_explanation,
        validation=validation_payload,
        stability=stability,
        generation_provenance=generation_provenance,
        final_test_targets=final_test_targets,
        repair_audit=repair_audit,
    )
    report["semantic_status"] = status
    pipeline_end_ts = time.time()
    pipeline_end_utc = datetime.now(timezone.utc).isoformat()
    report["pipeline_duration_seconds"] = round(pipeline_end_ts - pipeline_start_ts, 3)
    report["pipeline_start_utc"] = pipeline_start_utc
    report["pipeline_end_utc"] = pipeline_end_utc
    write_report(report, args.report_output)

    print(f"Pipeline finished: {status}")
    print(f"Generated tests: {test_output_path}")
    print(f"Report: {args.report_output}")
    if test_result["output"]:
        print("\nPytest output:\n")
        print(test_result["output"])

    if not validation_result["passed"]:
        return 2
    return 0 if semantic_passed else 1


def _quality_not_weaker(before: object, after: object) -> bool:
    try:
        before_values = [int(value) for value in before]  # type: ignore[union-attr]
        after_values = [int(value) for value in after]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return False
    return len(before_values) == len(after_values) and all(
        candidate >= original
        for original, candidate in zip(before_values, after_values, strict=True)
    )


def _rejected_generation_result(validation: dict[str, object]) -> dict[str, object]:
    issues = validation.get("issues", [])
    return {
        "command": "not executed: static validation failed",
        "return_code": 2,
        "passed": False,
        "output": "Generated tests rejected:\n" + "\n".join(str(item) for item in issues),
        "start_time_utc": None,
        "end_time_utc": None,
        "duration_seconds": 0.0,
        "timed_out": False,
        "summary": {
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0,
            "total": 1,
            "failing_node_ids": ["generated-test-validation"],
        },
    }


def _file_fingerprint(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def run_watch_mode(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Source file not found: {source_path}")
        return 1

    interval = max(args.watch_interval, 0.2)
    last_exit_code = run_pipeline(args)
    last_fingerprint = _file_fingerprint(source_path)
    print(f"Watching {source_path} for changes (interval: {interval:.1f}s). Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(interval)
            current_fingerprint = _file_fingerprint(source_path)
            if current_fingerprint == last_fingerprint:
                continue

            print("\nChange detected in source file. Rerunning pipeline...")
            last_fingerprint = current_fingerprint
            last_exit_code = run_pipeline(args)
    except KeyboardInterrupt:
        print("\nStopped watch mode.")
        return last_exit_code


if __name__ == "__main__":
    cli_args = parse_args()
    if cli_args.watch:
        sys.exit(run_watch_mode(cli_args))
    sys.exit(run_pipeline(cli_args))
