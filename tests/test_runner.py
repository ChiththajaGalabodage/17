from __future__ import annotations

from typing import Any
from pathlib import Path

import src.runner as runner


def _run_result(*, failing_node_ids: list[str], duration: float = 0.1) -> dict[str, Any]:
    failed = len(failing_node_ids)
    return {
        "return_code": 1 if failed else 0,
        "passed": failed == 0,
        "duration_seconds": duration,
        "summary": {
            "passed": 1,
            "failed": failed,
            "errors": 0,
            "failing_node_ids": failing_node_ids,
        },
    }


def test_parse_pytest_summary_preserves_counts_and_failure_identity() -> None:
    output = """
FAILED tests/test_alpha.py::test_wrong_total - AssertionError
ERROR tests/test_beta.py::test_setup - fixture 'db' not found
================ 2 failed, 3 passed, 1 error, 4 skipped, 1 xfailed, 1 xpassed ================
"""

    summary = runner.parse_pytest_summary(output)

    assert summary == {
        "passed": 3,
        "failed": 2,
        "errors": 1,
        "skipped": 4,
        "xfailed": 1,
        "xpassed": 1,
        "total": 12,
        "failing_node_ids": [
            "tests/test_alpha.py::test_wrong_total",
            "tests/test_beta.py::test_setup",
        ],
    }


def test_run_stability_is_consistent_when_failure_identity_is_repeated(monkeypatch) -> None:
    observations = iter(
        [
            _run_result(failing_node_ids=["tests/test_subject.py::test_total"]),
            _run_result(failing_node_ids=["tests/test_subject.py::test_total"]),
            _run_result(failing_node_ids=["tests/test_subject.py::test_total"]),
        ]
    )
    monkeypatch.setattr(
        runner,
        "run_pytest_targets",
        lambda _targets, **_kwargs: next(observations),
    )

    result = runner.run_stability(["tests/test_subject.py"], runs=3)

    assert result["consistent"] is True
    assert result["flaky"] is False
    assert result["all_passed"] is False
    assert result["unique_outcome_count"] == 1


def test_run_stability_detects_changed_failing_test_with_same_counts(monkeypatch) -> None:
    observations = iter(
        [
            _run_result(failing_node_ids=["tests/test_subject.py::test_alpha"]),
            _run_result(failing_node_ids=["tests/test_subject.py::test_beta"]),
        ]
    )
    monkeypatch.setattr(
        runner,
        "run_pytest_targets",
        lambda _targets, **_kwargs: next(observations),
    )

    result = runner.run_stability(["tests/test_subject.py"], runs=2)

    assert result["consistent"] is False
    assert result["flaky"] is True
    assert result["all_passed"] is False
    assert result["unique_outcome_count"] == 2


def test_run_stability_forwards_timeout_and_isolation(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(_targets: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _run_result(failing_node_ids=[])

    monkeypatch.setattr(runner, "run_pytest_targets", fake_run)

    result = runner.run_stability(
        ["tests/test_subject.py"],
        runs=2,
        isolated=True,
        timeout_seconds=1.25,
    )

    assert result["runs"] == 2
    assert calls == [
        {"isolated": True, "timeout_seconds": 1.25},
        {"isolated": True, "timeout_seconds": 1.25},
    ]


def test_isolated_runner_scrubs_secrets_and_discards_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_file = tmp_path / "tests" / "test_isolation.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_isolated():\n"
        "    assert os.getenv('GEMINI_API_KEY') is None\n"
        "    Path('generated-side-effect.txt').write_text('temporary', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")

    result = runner.run_pytest(str(test_file), isolated=True)

    assert result["passed"] is True
    assert result["isolated"] is True
    assert not (test_file.parent / "generated-side-effect.txt").exists()
