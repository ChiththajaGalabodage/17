from pathlib import Path

from src.coverage_evaluation import evaluate_coverage


def test_evaluate_coverage_reports_line_and_branch_counts(tmp_path: Path) -> None:
    (tmp_path / "subject.py").write_text(
        "def classify(value):\n"
        "    if value > 0:\n"
        "        return 'positive'\n"
        "    return 'other'\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests" / "test_subject.py"
    tests.parent.mkdir()
    tests.write_text(
        "from subject import classify\n\n"
        "def test_positive():\n"
        "    assert classify(1) == 'positive'\n",
        encoding="utf-8",
    )

    result = evaluate_coverage(
        "subject.py",
        ["tests/test_subject.py"],
        project_root=tmp_path,
        timeout_seconds=10,
    )

    assert result["valid"] is True
    assert result["tests_passed"] is True
    assert 0 < result["line_coverage_percent"] < 100
    assert 0 < result["branch_coverage_percent"] < 100
