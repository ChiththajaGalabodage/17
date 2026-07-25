import json
from pathlib import Path
from types import SimpleNamespace

from main import run_pipeline


def test_pipeline_returns_failure_when_generated_test_detects_product_bug(
    tmp_path: Path,
) -> None:
    source = tmp_path / "subject.py"
    generated = tmp_path / "test_generated_subject.py"
    report_path = tmp_path / "pipeline.json"
    source.write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        source=str(source),
        test_output=str(generated),
        report_output=str(report_path),
        max_heal_attempts=2,
        model="gemini-2.5-flash",
        temperature=0.0,
        seed=4885,
        offline=True,
        predictive_test_selection=False,
        selection_mode="change-impact",
        base_ref="HEAD~1",
        stability_runs=1,
        minimum_target_coverage=100.0,
        test_timeout=10.0,
    )

    exit_code = run_pipeline(args)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["semantic_status"] == "FAILED"
    assert report["validation"]["passed"] is True
    assert report["test_run"]["passed"] is False
    assert report["test_run"]["isolated"] is True
    assert report["heal_attempts"] == 0
    assert report["repair_audit"]["runtime_repair_attempts"] == 0
    assert report["repair_audit"]["protected_runtime_failures"] == 1
    assert report["generation_provenance"]["raw_generated_test_sha256"]
    assert report["generation_provenance"]["initial_normalized_test_sha256"]
    assert report["stability"]["consistent"] is True
    assert report["stability"]["all_passed"] is False


def test_live_llm_output_is_not_executed_without_explicit_containment_acknowledgement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "subject.py"
    generated = tmp_path / "test_generated_subject.py"
    report_path = tmp_path / "pipeline.json"
    source.write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")

    class _LiveGenerator:
        can_use_ai = True
        _api_calls = 1
        api_usage_records: list[dict[str, object]] = []

        def __init__(self, **_kwargs) -> None:
            pass

        def generate(self, _source: str, _analysis: dict[str, object]) -> dict[str, object]:
            return {
                "test_code": (
                    "import pytest\nfrom subject import add\n\n"
                    "def test_add():\n    assert add(1, 2) == 3\n"
                ),
                "explanation": [],
                "provenance": {"backend": "gemini", "api_calls": 1},
            }

    monkeypatch.setattr("main.GeminiTestGenerator", _LiveGenerator)
    args = SimpleNamespace(
        source=str(source),
        test_output=str(generated),
        report_output=str(report_path),
        max_heal_attempts=0,
        model="fake-live-model",
        temperature=0.0,
        seed=4885,
        offline=False,
        predictive_test_selection=False,
        selection_mode="change-impact",
        base_ref="HEAD~1",
        stability_runs=1,
        minimum_target_coverage=100.0,
        test_timeout=10.0,
        allow_uncontained_llm_tests=False,
    )

    exit_code = run_pipeline(args)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert report["test_run"]["command"] == "not executed: static validation failed"
    assert report["generation_provenance"]["execution_policy"]["runner_security_boundary"] is False
    assert any("not an OS security boundary" in issue for issue in report["validation"]["issues"])
