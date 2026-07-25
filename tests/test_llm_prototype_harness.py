from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import llm_prototype_harness as harness


def test_run_prototype_preserves_nonzero_pipeline_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "subject.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    def fake_pipeline(args: Namespace) -> int:
        Path(args.report_output).write_text(
            json.dumps({"test_run": {"passed": False}, "heal_attempts": 0}),
            encoding="utf-8",
        )
        return 2

    monkeypatch.setattr(harness, "run_pipeline", fake_pipeline)

    with pytest.raises(harness.PrototypePipelineFailure) as caught:
        harness.run_prototype(
            str(source),
            output=str(tmp_path / "generated_test.py"),
            report_output=str(report_path),
        )

    assert caught.value.exit_code == 2
    assert caught.value.report["pipeline_exit_code"] == 2


def test_main_exits_with_pipeline_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness, "load_local_env", lambda: None)
    monkeypatch.setattr(
        harness,
        "_parse_args",
        lambda: Namespace(source="subject.py", output=None, report_output=None),
    )

    def fail_prototype(*_args: object) -> dict[str, object]:
        raise harness.PrototypePipelineFailure(3, {})

    monkeypatch.setattr(harness, "run_prototype", fail_prototype)

    with pytest.raises(SystemExit) as caught:
        harness.main()

    assert caught.value.code == 3
