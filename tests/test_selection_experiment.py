from pathlib import Path

from src.selection_experiment import (
    SelectionExperimentConfig,
    SelectionExperimentRunner,
    SelectionScenario,
)


def test_demo_selection_scenario_is_measured_but_not_claim_ready(tmp_path: Path) -> None:
    (tmp_path / "subject.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_subject.py").write_text(
        "from subject import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (tests_dir / "test_other.py").write_text(
        "def test_other():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    scenario = SelectionScenario(
        scenario_id="demo",
        changed_files=("subject.py",),
        relevant_tests=("tests/test_subject.py",),
        role="demo",
        oracle_source="unit-test oracle",
    )
    config = SelectionExperimentConfig(
        repo_root=tmp_path,
        scenarios=(scenario,),
        runs=1,
        offline=True,
        output_root=Path("reports"),
        run_id="unit",
    )

    result = SelectionExperimentRunner(config).run()

    measured = result["scenarios"][0]
    assert measured["baseline"]["metrics"]["recall"] == 1.0
    assert measured["baseline"]["metrics"]["test_reduction"] == 0.5
    assert measured["proposed_runs"][0]["evidence"]["backend"] == "deterministic"
    assert result["summary"]["claim_readiness"]["ready"] is False
    assert result["summary"]["evidence_readiness"]["ready"] is False
    assert result["summary"]["claim_support"]["assessed"] is False
