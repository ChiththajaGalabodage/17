from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.mutation_testing import BaselineFailure, evaluate_mutations, generate_mutants


def _write_project(root: Path, *, source: str, tests: str) -> tuple[Path, Path]:
    source_path = root / "subject.py"
    tests_path = root / "tests" / "test_subject.py"
    tests_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    tests_path.write_text(tests, encoding="utf-8")
    return source_path, tests_path


def _operator_fingerprint(source: str) -> list[str]:
    fingerprint: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.BinOp, ast.AugAssign, ast.BoolOp)):
            fingerprint.append(type(node.op).__name__)
        elif isinstance(node, ast.Compare):
            fingerprint.extend(type(operator).__name__ for operator in node.ops)
    return fingerprint


def test_generate_mutants_is_deterministic_and_changes_one_operator_site() -> None:
    source = (
        "def evaluate(left, right, ready):\n"
        "    total = left + right\n"
        "    return total > right and ready\n"
    )

    first = generate_mutants(source, filename="subject.py")
    second = generate_mutants(source, filename="subject.py")

    assert {mutant.category for mutant in first} == {"arithmetic", "comparison", "boolean"}
    assert [mutant.mutant_id for mutant in first] == [mutant.mutant_id for mutant in second]
    assert len({mutant.mutant_id for mutant in first}) == len(first)
    assert len({mutant.source_sha256 for mutant in first}) == len(first)

    original_fingerprint = _operator_fingerprint(source)
    for mutant in first:
        compile(mutant.source_code, "subject.py", "exec")
        mutant_fingerprint = _operator_fingerprint(mutant.source_code)
        assert len(mutant_fingerprint) == len(original_fingerprint)
        assert sum(
            original != changed
            for original, changed in zip(original_fingerprint, mutant_fingerprint, strict=True)
        ) == 1
        assert mutant.original_operator != mutant.replacement_operator
        assert mutant.lineno > 0


def test_generate_mutants_changes_one_boolean_connector_at_a_time() -> None:
    mutants = generate_mutants(
        "def all_ready(first, second, third):\n    return first and second and third\n"
    )
    boolean_mutants = [mutant for mutant in mutants if mutant.category == "boolean"]

    assert len(boolean_mutants) == 2
    for mutant in boolean_mutants:
        assert mutant.source_code.count(" and ") == 1
        assert mutant.source_code.count(" or ") == 1


def test_evaluate_mutations_uses_isolated_copies_and_scrubs_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, tests_path = _write_project(
        tmp_path,
        source=(
            "def calculate(left, right):\n"
            "    return left + right\n\n"
            "def accepts(value, ready):\n"
            "    return value > 0 and ready\n"
        ),
        tests=(
            "import os\n"
            "from pathlib import Path\n"
            "from subject import accepts, calculate\n\n"
            "def test_calculate():\n"
            "    assert calculate(2, 3) == 5\n\n"
            "def test_accepts():\n"
            "    assert accepts(1, True) is True\n"
            "    assert accepts(-1, True) is False\n"
            "    assert accepts(1, False) is False\n\n"
            "def test_host_secrets_are_not_available():\n"
            "    assert os.getenv('GEMINI_API_KEY') is None\n"
            "    assert not Path('.env').exists()\n"
        ),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "process-secret")

    original_source = source_path.read_bytes()
    original_tests = tests_path.read_bytes()
    report = evaluate_mutations(
        "subject.py",
        ["tests/test_subject.py"],
        project_root=tmp_path,
        timeout_seconds=5,
    )

    assert report.baseline.passed is True
    assert report.invalid_ids == ()
    assert report.timed_out_ids == ()
    assert report.survived_ids == ()
    assert set(report.valid_ids) == set(report.killed_ids)
    assert len(report.killed_ids) == 3
    assert report.mutation_score == 100.0
    assert source_path.read_bytes() == original_source
    assert tests_path.read_bytes() == original_tests
    assert env_file.read_text(encoding="utf-8") == "GEMINI_API_KEY=file-secret\n"

    serialized = report.to_dict()
    assert serialized["mutation_score"] == 100.0
    assert serialized["killed_mutant_ids"] == list(report.killed_ids)
    assert serialized["score_excludes_invalid_and_timed_out"] is True
    assert "process-secret" not in str(serialized)
    assert "file-secret" not in str(serialized)


def test_evaluate_mutations_requires_a_passing_original_suite(tmp_path: Path) -> None:
    source_path, tests_path = _write_project(
        tmp_path,
        source="def calculate(left, right):\n    return left - right\n",
        tests=(
            "from subject import calculate\n\n"
            "def test_calculate():\n"
            "    assert calculate(2, 3) == 5\n"
        ),
    )
    original_source = source_path.read_bytes()
    original_tests = tests_path.read_bytes()

    with pytest.raises(BaselineFailure) as captured:
        evaluate_mutations(
            source_path,
            [tests_path],
            project_root=tmp_path,
            timeout_seconds=5,
        )

    assert captured.value.result.return_code == 1
    assert captured.value.result.passed is False
    assert source_path.read_bytes() == original_source
    assert tests_path.read_bytes() == original_tests


def test_evaluate_mutations_reports_timed_out_mutants_separately(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        source=(
            "def count_to(limit):\n"
            "    count = 0\n"
            "    while count < limit:\n"
            "        count += 1\n"
            "    return count\n"
        ),
        tests=(
            "from subject import count_to\n\n"
            "def test_count_to():\n"
            "    assert count_to(1) == 1\n"
        ),
    )

    report = evaluate_mutations(
        "subject.py",
        ["tests/test_subject.py::test_count_to"],
        project_root=tmp_path,
        timeout_seconds=2.5,
    )

    assert len(report.timed_out_ids) == 1
    assert len(report.killed_ids) == 1
    assert report.survived_ids == ()
    assert report.invalid_ids == ()
    assert set(report.valid_ids) == set(report.timed_out_ids) | set(report.killed_ids)
    assert report.mutation_score == 100.0
    timed_out = next(item for item in report.mutants if item.status == "timed_out")
    assert timed_out.test_run is not None
    assert timed_out.test_run.timed_out is True
    assert timed_out.test_run.return_code is None


def test_evaluate_mutations_rejects_paths_outside_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside.py"
    project.mkdir()
    outside.write_text("value = 1\n", encoding="utf-8")
    (project / "test_subject.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the project root"):
        evaluate_mutations(
            outside,
            ["test_subject.py"],
            project_root=project,
        )
