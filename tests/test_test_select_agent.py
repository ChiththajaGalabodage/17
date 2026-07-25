from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from src.test_select_agent import TestSelectAgent


class _FakeModels:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    def generate_content(self, *, model: str, contents: str, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self.response_text)


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.models = _FakeModels(response_text)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample_repo(tmp_path: Path) -> Path:
    _write(tmp_path / "calculator.py", "def add(left, right):\n    return left + right\n")
    _write(
        tmp_path / "tests" / "test_calculator.py",
        "from calculator import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    _write(
        tmp_path / "tests" / "test_other.py",
        "def test_other():\n    assert 2 + 2 == 4\n",
    )
    return tmp_path


def test_deterministic_selection_returns_evidence_and_preserves_list_api(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    selector = TestSelectAgent(str(repo), use_llm=False)

    result = selector.select_with_evidence(["calculator.py"])

    assert result["universe"] == ["tests/test_calculator.py", "tests/test_other.py"]
    assert result["selected"] == ["tests/test_calculator.py"]
    assert result["backend"] == "deterministic"
    assert result["fallback"] is False
    assert result["selection_ratio"] == 0.5
    assert any(
        reason.startswith("deterministic:imports-changed-module:calculator")
        for reason in result["reasons"]["tests/test_calculator.py"]
    )
    assert selector.select_tests(["calculator.py"]) == result["selected"]


def test_deterministic_selector_fails_open_to_full_universe(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    selector = TestSelectAgent(str(repo), use_llm=False)

    result = selector.select_with_evidence(["README.md"])

    assert result["selected"] == result["universe"]
    assert result["fallback"] is True
    assert result["fallback_reason"] == "no-deterministic-impact-match"
    assert result["selection_ratio"] == 1.0
    assert all(result["reasons"][path] for path in result["selected"])


def test_explicit_diff_symbols_avoid_module_wide_overselection(tmp_path: Path) -> None:
    _write(
        tmp_path / "calculator.py",
        "def add(left, right):\n    return left + right\n\n"
        "def subtract(left, right):\n    return left - right\n",
    )
    _write(
        tmp_path / "tests" / "test_add.py",
        "from calculator import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    _write(
        tmp_path / "tests" / "test_subtract.py",
        "from calculator import subtract\n\ndef test_subtract():\n    assert subtract(2, 1) == 1\n",
    )
    selector = TestSelectAgent(str(tmp_path), use_llm=False)

    result = selector.select_with_evidence(
        ["calculator.py"],
        changed_symbols=["add"],
    )

    assert result["selected"] == ["tests/test_add.py"]
    assert result["changed_symbols"] == ["add"]


def test_git_diff_hunks_map_to_changed_definition(tmp_path: Path) -> None:
    _write(
        tmp_path / "calculator.py",
        "def add(left, right):\n    return left + right\n\n"
        "def subtract(left, right):\n    return left - right\n",
    )
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "research@example.invalid"],
        ["git", "config", "user.name", "Research Test"],
        ["git", "add", "calculator.py"],
        ["git", "commit", "-m", "baseline"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    _write(
        tmp_path / "calculator.py",
        "def add(left, right):\n    return left + right + 0\n\n"
        "def subtract(left, right):\n    return left - right\n",
    )

    selector = TestSelectAgent(str(tmp_path), use_llm=False)

    assert selector.get_changed_symbols(base_ref="HEAD") == ["add"]


def test_valid_gemini_selection_is_allowlisted_and_auditable(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    response = json.dumps(
        {
            "selected": ["tests/test_other.py"],
            "reasons": {"tests/test_other.py": "README behavior is asserted here"},
        }
    )
    client = _FakeClient(response)
    selector = TestSelectAgent(str(repo), llm_client=client, model="fake-model")
    context = selector.build_change_context(["calculator.py"], ["add"])

    result = selector.select_with_evidence(["README.md"], change_context=context)

    assert result["selected"] == ["tests/test_other.py"]
    assert result["backend"] == "gemini-hybrid"
    assert result["fallback"] is False
    assert result["selection_ratio"] == 0.5
    assert result["model"] == "fake-model"
    assert result["prompt_sha256"]
    assert result["response_sha256"]
    assert result["change_context_sha256"]
    assert result["reasons"]["tests/test_other.py"] == [
        "llm:README behavior is asserted here"
    ]
    assert len(client.models.calls) == 1
    prompt = client.models.calls[0]["contents"]
    assert "tests/test_calculator.py" in prompt
    assert "tests/not_allowed.py" not in prompt
    assert "referenced_identifiers" in prompt


def test_structural_change_context_excludes_source_literals(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    _write(
        repo / "calculator.py",
        "API_TOKEN = 'do-not-send-this'\n\ndef add(left, right):\n    return left + right\n",
    )
    selector = TestSelectAgent(str(repo), use_llm=False)

    context = selector.build_change_context(["calculator.py"], ["add"])
    serialized = json.dumps(context)

    assert "do-not-send-this" not in serialized
    assert context["modules"][0]["changed_definitions"][0]["name"] == "add"


def test_gemini_cannot_drop_deterministic_matches(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    response = json.dumps(
        {
            "selected": ["tests/test_other.py"],
            "reasons": {"tests/test_other.py": "additional impacted behavior"},
        }
    )
    selector = TestSelectAgent(str(repo), llm_client=_FakeClient(response))

    result = selector.select_with_evidence(["calculator.py"])

    assert result["selected"] == ["tests/test_calculator.py", "tests/test_other.py"]
    assert any(
        reason.startswith("deterministic:")
        for reason in result["reasons"]["tests/test_calculator.py"]
    )


def test_invalid_gemini_path_uses_deterministic_fail_open_result(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    response = json.dumps(
        {
            "selected": ["tests/not_allowed.py"],
            "reasons": {"tests/not_allowed.py": "invented path"},
        }
    )
    client = _FakeClient(response)
    selector = TestSelectAgent(str(repo), llm_client=client)

    result = selector.select_with_evidence(["README.md"])

    assert result["backend"] == "deterministic"
    assert result["fallback"] is True
    assert "invalid-llm-selection:ValueError" in result["fallback_reason"]
    assert result["selected"] == result["universe"]
    assert "tests/not_allowed.py" not in result["selected"]


def test_missing_llm_configuration_falls_back_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _sample_repo(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    selector = TestSelectAgent(str(repo), api_key=None, use_llm=True)

    result = selector.select_with_evidence(["calculator.py"])

    assert result["backend"] == "deterministic"
    assert result["fallback"] is True
    assert result["fallback_reason"].startswith("llm-unavailable:no-api-key")
    assert result["selected"] == ["tests/test_calculator.py"]
