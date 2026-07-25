import json
from pathlib import Path

import pytest

from src.healing_experiment import (
    HealingExperimentConfig,
    HealingExperimentRunner,
    HealingScenario,
    build_healing_summary,
    load_healing_manifest,
)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage_metadata = {"prompt_tokens": 10, "output_tokens": 10}
        self.model_version = "fake-live-model"
        self.response_id = "fake-response"


class _Models:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls = 0

    def generate_content(self, **_kwargs) -> _Response:
        self.calls += 1
        return _Response(self.response_text)


class _FakeGeminiGenerator:
    can_use_ai = True
    model = "fake-live-gemini"
    temperature = 0.0
    seed = 4885

    def __init__(self, response_text: str) -> None:
        self._client = type("Client", (), {"models": _Models(response_text)})()
        self._api_calls = 0
        self.api_usage_records: list[dict[str, object]] = []

    def record_api_response(self, response: _Response, *, phase: str) -> None:
        self.api_usage_records.append(
            {
                "phase": phase,
                "usage": response.usage_metadata,
                "model_version": response.model_version,
                "response_id": response.response_id,
            }
        )


def _make_fake_repo(tmp_path: Path) -> tuple[Path, tuple[HealingScenario, ...], str]:
    root = tmp_path / "project"
    package = root / "sample_project"
    package.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "main.py").write_text("# repository marker\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "subject.py").write_text(
        "def add(left, right):\n"
        "    return left + right\n\n"
        "def divide(left, right):\n"
        "    if right == 0:\n"
        "        raise ValueError('right must not be zero')\n"
        "    return left / right\n",
        encoding="utf-8",
    )
    positive_broken = (
        "from sample_project.subject import add\n\n"
        "@pytest.mark.parametrize(\n"
        "    ('left', 'right', 'expected'),\n"
        "    [(1, 2, 3), (-1, 1, 0)],\n"
        ")\n"
        "def test_add_contract(left, right, expected):\n"
        "    assert add(left, right) == expected\n"
    )
    positive_reference = "import pytest\n" + positive_broken
    negative_broken = (
        "from sample_project.subject import add\n\n"
        "def test_product_failure_is_preserved():\n"
        "    assert add(1, 2) == 4\n"
    )
    negative_reference = negative_broken.replace("== 4", "== 3")
    files = {
        "positive_broken_case.py": positive_broken,
        "positive_reference_case.py": positive_reference,
        "negative_broken_case.py": negative_broken,
        "negative_reference_case.py": negative_reference,
    }
    for name, code in files.items():
        (package / name).write_text(code, encoding="utf-8")

    scenarios = (
        HealingScenario(
            scenario_id="positive",
            project_id="sample",
            role="demo",
            artifact_type="missing-import",
            source="sample_project/subject.py",
            broken_test="sample_project/positive_broken_case.py",
            reference_test="sample_project/positive_reference_case.py",
            expected_safe_to_heal=True,
            mutation_limit=1,
        ),
        HealingScenario(
            scenario_id="negative",
            project_id="sample",
            role="demo",
            artifact_type="product-failure",
            source="sample_project/subject.py",
            broken_test="sample_project/negative_broken_case.py",
            reference_test="sample_project/negative_reference_case.py",
            expected_safe_to_heal=False,
            mutation_limit=1,
        ),
    )
    return root, scenarios, positive_reference


def test_controlled_arms_preserve_negative_and_repair_positive(tmp_path: Path) -> None:
    root, scenarios, positive_reference = _make_fake_repo(tmp_path)
    response = json.dumps(
        {
            "test_code": positive_reference.splitlines(),
            "explanation": ["Restored the missing pytest import only."],
        }
    )
    generator = _FakeGeminiGenerator(response)
    config = HealingExperimentConfig(
        repo_root=root,
        scenarios=scenarios,
        output_root=Path("reports/healing"),
        run_id="three-arm-demo",
        offline=False,
        test_timeout_seconds=15.0,
        mutation_timeout_seconds=15.0,
    )

    payload = HealingExperimentRunner(config, ai_generator=generator).run()

    assert payload["summary"]["invalid_scenario_count"] == 0
    assert payload["summary"]["evidence_readiness"]["ready"] is False
    assert "demo scenarios only" in " ".join(
        payload["summary"]["evidence_readiness"]["reasons"]
    )
    metrics = payload["summary"]["all_scenario_metrics"]["classification"]
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0

    by_id = {
        item["scenario"]["scenario_id"]: item for item in payload["scenarios"]
    }
    positive = by_id["positive"]
    assert positive["arms"]["no_heal"]["outcome"]["passed"] is False
    assert positive["arms"]["deterministic"]["action"] == "repaired-import"
    assert positive["arms"]["deterministic"]["repair_success"] is True
    assert positive["arms"]["deterministic"]["before_sha256"] != positive[
        "arms"
    ]["deterministic"]["after_sha256"]
    assert positive["arms"]["deterministic"]["semantic_retention"]["retained"] is True
    assert positive["arms"]["gemini"]["repair_success"] is True
    assert positive["arms"]["gemini"]["provenance"]["backend"] == "gemini"
    assert positive["arms"]["gemini"]["provenance"]["api_calls"] == 1

    negative = by_id["negative"]
    assert negative["arms"]["deterministic"]["action"] == "not-healed"
    assert negative["arms"]["deterministic"]["unsafe_heal"] is False
    assert negative["arms"]["deterministic"]["before_sha256"] == negative[
        "arms"
    ]["deterministic"]["after_sha256"]
    assert negative["arms"]["gemini"]["unsafe_heal"] is False
    assert negative["arms"]["gemini"]["provenance"]["api_calls"] == 0
    assert generator._client.models.calls == 1


def test_label_disagreement_fails_closed_before_repair_arms(tmp_path: Path) -> None:
    root, scenarios, _reference = _make_fake_repo(tmp_path)
    mislabeled = HealingScenario(
        scenario_id="mislabeled",
        project_id="sample",
        role="demo",
        artifact_type="incorrect-label",
        source=scenarios[0].source,
        broken_test=scenarios[0].broken_test,
        reference_test=scenarios[0].reference_test,
        expected_safe_to_heal=False,
        mutation_limit=1,
    )
    config = HealingExperimentConfig(
        repo_root=root,
        scenarios=(mislabeled,),
        output_root=Path("reports/healing"),
        run_id="invalid-label",
        offline=True,
        test_timeout_seconds=15.0,
        mutation_timeout_seconds=15.0,
    )

    payload = HealingExperimentRunner(config).run()
    result = payload["scenarios"][0]

    assert result["status"] == "invalid"
    assert "disagrees" in " ".join(result["invalid_reasons"])
    assert set(result["arms"]) == {"no_heal"}
    assert payload["summary"]["invalid_scenario_count"] == 1


def test_evidence_readiness_is_outcome_neutral_and_live_llm_is_gated(
    tmp_path: Path,
) -> None:
    results: list[dict[str, object]] = []
    for index in range(30):
        expected = index < 15
        arms = {
            name: {
                "available": name != "gemini",
                "repair_success": False if expected else None,
                "unsafe_heal": True if (not expected and name == "deterministic") else False,
                "oracle_matches_human_reference": False,
                "semantic_retention": {"available": False, "retained": None},
                "outcome": {"executed": True, "duration_seconds": 0.01},
                "provenance": {
                    "provider_available": name != "gemini",
                    "live_model_response": False,
                },
            }
            for name in ("no_heal", "deterministic", "gemini")
        }
        results.append(
            {
                "status": "valid",
                "scenario": {
                    "scenario_id": f"scenario-{index}",
                    "project_id": f"project-{index % 3}",
                    "role": "study",
                    "expected_safe_to_heal": expected,
                },
                "classification": {"safe_to_heal": expected},
                "arms": arms,
            }
        )

    config = HealingExperimentConfig(repo_root=tmp_path, scenarios=(), offline=True)
    summary = build_healing_summary(config, results)
    assert summary["evidence_readiness"]["ready"] is True
    assert summary["claim_support"]["assessed"] is False
    assert summary["claim_support"]["supported"] is False

    config.claim_llm_effect = True
    gated = build_healing_summary(config, results)
    assert gated["evidence_readiness"]["ready"] is False
    assert any(
        "live Gemini" in reason
        for reason in gated["evidence_readiness"]["reasons"]
    )


def test_manifest_rejects_non_boolean_ground_truth(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenarios": [
                    {
                        "id": "bad",
                        "project_id": "project",
                        "source": "subject.py",
                        "broken_test": "broken.py",
                        "reference_test": "reference.py",
                        "expected_safe_to_heal": "false",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="JSON boolean"):
        load_healing_manifest(manifest)
