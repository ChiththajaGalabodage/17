"""Controlled, evidence-preserving evaluation of constrained test healing.

The ordinary pipeline only encounters repair opportunities by chance.  This
module instead replays the same frozen, labelled broken test through a no-heal
control, the deterministic repair path, and (when configured) the live Gemini
repair path.  A human-corrected test is the executable reference oracle.

Evidence readiness in this module describes protocol completeness only.  It is
deliberately separate from claim support, so an adequately sized experiment
with poor outcomes remains visible as valid negative evidence.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analyzer import analyze_code
from src.generator import GeminiTestGenerator
from src.healer import classify_failure, heal_test_bundle, preserves_oracle_strength
from src.mutation_testing import (
    BaselineFailure,
    MutationEvaluationError,
    MutationReport,
    evaluate_mutations,
)
from src.research_metrics import (
    descriptive_statistics,
    fault_detection_metrics,
    selection_metrics,
)
from src.runner import run_pytest
from src.validator import validate_generated_test_code


@dataclass(frozen=True, slots=True)
class HealingScenario:
    """One frozen broken test and its manually corrected reference."""

    scenario_id: str
    project_id: str
    source: str
    broken_test: str
    reference_test: str
    expected_safe_to_heal: bool
    role: str = "study"
    artifact_type: str = "unspecified"
    mutation_limit: int = 10

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HealingScenario":
        required = {
            "id",
            "project_id",
            "source",
            "broken_test",
            "reference_test",
            "expected_safe_to_heal",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(
                "Healing scenario is missing required fields: " + ", ".join(missing)
            )
        if type(payload["expected_safe_to_heal"]) is not bool:
            raise ValueError("expected_safe_to_heal must be a JSON boolean")
        mutation_limit = payload.get("mutation_limit", 10)
        if type(mutation_limit) is not int or mutation_limit <= 0:
            raise ValueError("mutation_limit must be a positive integer")
        role = str(payload.get("role", "study"))
        if role not in {"demo", "study"}:
            raise ValueError("Healing scenario role must be 'demo' or 'study'")
        return cls(
            scenario_id=str(payload["id"]),
            project_id=str(payload["project_id"]),
            source=str(payload["source"]),
            broken_test=str(payload["broken_test"]),
            reference_test=str(payload["reference_test"]),
            expected_safe_to_heal=payload["expected_safe_to_heal"],
            role=role,
            artifact_type=str(payload.get("artifact_type", "unspecified")),
            mutation_limit=mutation_limit,
        )


@dataclass(slots=True)
class HealingExperimentConfig:
    repo_root: Path
    scenarios: tuple[HealingScenario, ...]
    output_root: Path = Path("reports/healing_runs")
    run_id: str | None = None
    offline: bool = False
    model: str = "gemini-2.5-flash"
    temperature: float = 0.0
    seed: int = 4885
    test_timeout_seconds: float = 30.0
    mutation_timeout_seconds: float = 30.0
    mutation_limit_override: int | None = None
    minimum_study_scenarios: int = 30
    minimum_study_projects: int = 3
    claim_llm_effect: bool = False

    def validate(self) -> None:
        self.repo_root = self.repo_root.resolve()
        if not self.repo_root.is_dir():
            raise FileNotFoundError(f"Repository root not found: {self.repo_root}")
        if not self.scenarios:
            raise ValueError("At least one healing scenario is required")
        if self.test_timeout_seconds <= 0 or self.mutation_timeout_seconds <= 0:
            raise ValueError("Experiment timeouts must be positive")
        if self.mutation_limit_override is not None and self.mutation_limit_override <= 0:
            raise ValueError("mutation_limit_override must be positive when provided")
        if self.minimum_study_scenarios <= 0 or self.minimum_study_projects <= 0:
            raise ValueError("Study readiness thresholds must be positive")

        identifiers = [scenario.scenario_id for scenario in self.scenarios]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Healing scenario IDs must be unique")
        for scenario in self.scenarios:
            if not scenario.scenario_id.strip() or not scenario.project_id.strip():
                raise ValueError("Scenario and project IDs must not be empty")
            if scenario.role not in {"demo", "study"}:
                raise ValueError("Healing scenario role must be 'demo' or 'study'")
            source = _require_relative_file(self.repo_root, scenario.source, "source")
            broken = _require_relative_file(
                self.repo_root, scenario.broken_test, "broken test"
            )
            reference = _require_relative_file(
                self.repo_root, scenario.reference_test, "reference test"
            )
            if source.suffix.lower() != ".py":
                raise ValueError("Healing scenario source must be a Python file")
            if broken.suffix.lower() != ".py" or reference.suffix.lower() != ".py":
                raise ValueError("Broken and reference tests must be Python files")
            if broken == reference:
                raise ValueError("Broken and reference tests must be distinct files")


class HealingExperimentRunner:
    """Execute identical labelled scenarios through all configured arms."""

    def __init__(
        self,
        config: HealingExperimentConfig,
        *,
        ai_generator: Any | None = None,
    ) -> None:
        self.config = config
        self._provided_generator = ai_generator
        self._generator: Any | None = None

    def run(self) -> dict[str, Any]:
        self.config.validate()
        run_id = self.config.run_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        output_dir = _resolve_output_directory(
            self.config.repo_root, self.config.output_root, run_id
        )
        output_dir.mkdir(parents=True, exist_ok=False)

        provenance = _collect_provenance(self.config)
        _write_json(output_dir / "provenance.json", provenance)
        self._generator = self._resolve_generator()

        results: list[dict[str, Any]] = []
        for scenario in self.config.scenarios:
            try:
                result = self._run_scenario(scenario, output_dir)
            except Exception as error:  # fail closed while preserving the other cases
                result = {
                    "scenario": asdict(scenario),
                    "status": "invalid",
                    "invalid_reason": f"Unexpected scenario error: {type(error).__name__}: {error}",
                    "arms": {},
                }
                scenario_dir = output_dir / "raw" / _safe_name(scenario.scenario_id)
                scenario_dir.mkdir(parents=True, exist_ok=True)
                _write_json(scenario_dir / "scenario_result.json", result)
            results.append(result)

        summary = build_healing_summary(self.config, results)
        payload = {
            "schema_version": 1,
            "experiment_id": run_id,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiment_directory": _relative_or_absolute(
                output_dir, self.config.repo_root
            ),
            "config": _config_payload(self.config),
            "provenance": provenance,
            "scenarios": results,
            "summary": summary,
        }
        _write_json(output_dir / "summary.json", payload)
        _write_results_csv(output_dir / "results.csv", results)
        _write_markdown_report(output_dir / "healing_report.md", payload)
        return payload

    def _resolve_generator(self) -> Any | None:
        if self.config.offline:
            return None
        if self._provided_generator is not None:
            return self._provided_generator
        generator = GeminiTestGenerator(
            model=self.config.model,
            temperature=self.config.temperature,
            seed=self.config.seed,
        )
        return generator if generator.can_use_ai else None

    def _run_scenario(
        self,
        scenario: HealingScenario,
        output_dir: Path,
    ) -> dict[str, Any]:
        scenario_dir = output_dir / "raw" / _safe_name(scenario.scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=False)
        source_path = (self.config.repo_root / scenario.source).resolve()
        broken_path = (self.config.repo_root / scenario.broken_test).resolve()
        reference_path = (self.config.repo_root / scenario.reference_test).resolve()
        source_code = source_path.read_text(encoding="utf-8")
        broken_code = broken_path.read_text(encoding="utf-8")
        reference_code = reference_path.read_text(encoding="utf-8")
        source_sha256 = _sha256(source_code)
        analysis = analyze_code(str(source_path))

        (scenario_dir / "before.py").write_text(broken_code, encoding="utf-8")
        (scenario_dir / "human_reference.py").write_text(
            reference_code, encoding="utf-8"
        )

        no_heal_dir = scenario_dir / "no_heal"
        no_heal_dir.mkdir()
        no_heal_path = no_heal_dir / "test_candidate.py"
        no_heal_path.write_text(broken_code, encoding="utf-8")
        initial_validation = validate_generated_test_code(
            broken_code, source_path, analysis, minimum_target_coverage=0.0
        )
        initial_run = run_pytest(
            str(no_heal_path),
            timeout_seconds=self.config.test_timeout_seconds,
            isolated=True,
        )
        classification = classify_failure(initial_run.get("output", ""))

        reference_validation = validate_generated_test_code(
            reference_code, source_path, analysis, minimum_target_coverage=0.0
        )
        reference_run = run_pytest(
            str(reference_path),
            timeout_seconds=self.config.test_timeout_seconds,
            isolated=True,
        )

        base: dict[str, Any] = {
            "scenario": asdict(scenario),
            "status": "validating",
            "source_sha256_before": source_sha256,
            "broken_test_sha256": _sha256(broken_code),
            "reference_test_sha256": _sha256(reference_code),
            "classification": classification,
            "initial_validation": initial_validation,
            "initial_outcome": initial_run,
            "reference_validation": reference_validation,
            "reference_outcome": reference_run,
            "arms": {},
        }

        invalid_reasons: list[str] = []
        if initial_run.get("timed_out"):
            invalid_reasons.append("The frozen broken test timed out")
        elif initial_run.get("passed"):
            invalid_reasons.append("The frozen broken test unexpectedly passed")
        if not reference_validation.get("passed"):
            invalid_reasons.append("The human-corrected reference failed static validation")
        if reference_run.get("timed_out") or not reference_run.get("passed"):
            invalid_reasons.append("The human-corrected reference did not pass in isolation")
        if bool(classification.get("safe_to_heal")) != scenario.expected_safe_to_heal:
            invalid_reasons.append(
                "Observed failure classification disagrees with expected_safe_to_heal"
            )
        if _sha256(source_path.read_text(encoding="utf-8")) != source_sha256:
            invalid_reasons.append("The source changed during initial execution")

        base["arms"]["no_heal"] = _control_arm_record(
            broken_code=broken_code,
            reference_code=reference_code,
            classification=classification,
            validation=initial_validation,
            outcome=initial_run,
            expected_safe_to_heal=scenario.expected_safe_to_heal,
            source_sha256=source_sha256,
        )
        _write_json(no_heal_dir / "arm_result.json", base["arms"]["no_heal"])

        if invalid_reasons:
            base["status"] = "invalid"
            base["invalid_reasons"] = invalid_reasons
            base["source_sha256_after"] = _sha256(
                source_path.read_text(encoding="utf-8")
            )
            _write_json(scenario_dir / "scenario_result.json", base)
            return base

        mutation_limit = (
            self.config.mutation_limit_override
            if self.config.mutation_limit_override is not None
            else scenario.mutation_limit
        )
        try:
            reference_mutation = evaluate_mutations(
                source_path=scenario.source,
                test_targets=[scenario.reference_test],
                project_root=self.config.repo_root,
                timeout_seconds=self.config.mutation_timeout_seconds,
                max_mutants=mutation_limit,
            )
        except (BaselineFailure, MutationEvaluationError, ValueError) as error:
            base["status"] = "invalid"
            base["invalid_reasons"] = [
                f"Reference mutation evaluation failed closed: {type(error).__name__}: {error}"
            ]
            base["source_sha256_after"] = _sha256(
                source_path.read_text(encoding="utf-8")
            )
            _write_json(scenario_dir / "scenario_result.json", base)
            return base

        reference_mutation_payload = reference_mutation.to_dict()
        base["reference_mutation"] = reference_mutation_payload
        _write_json(scenario_dir / "reference_mutation.json", reference_mutation_payload)

        deterministic_bundle = heal_test_bundle(
            current_test_code=broken_code,
            test_output=initial_run["output"],
            analysis=analysis,
            ai_generator=None,
        )
        base["arms"]["deterministic"] = self._evaluate_repair_arm(
            arm="deterministic",
            scenario=scenario,
            scenario_dir=scenario_dir,
            source_path=source_path,
            source_sha256=source_sha256,
            analysis=analysis,
            broken_code=broken_code,
            reference_code=reference_code,
            classification=classification,
            bundle=deterministic_bundle,
            reference_mutation=reference_mutation,
            mutation_limit=mutation_limit,
            provenance={
                "backend": "deterministic",
                "model": None,
                "api_calls": 0,
                "live_model_response": False,
                "provider_available": True,
            },
        )

        if self._generator is None:
            base["arms"]["gemini"] = _unavailable_gemini_arm(
                self.config, classification
            )
            gemini_dir = scenario_dir / "gemini"
            gemini_dir.mkdir(exist_ok=False)
            _write_json(gemini_dir / "arm_result.json", base["arms"]["gemini"])
        else:
            calls_before = int(getattr(self._generator, "_api_calls", 0))
            usage_before = len(getattr(self._generator, "api_usage_records", []))
            gemini_bundle = heal_test_bundle(
                current_test_code=broken_code,
                test_output=initial_run["output"],
                analysis=analysis,
                ai_generator=self._generator,
            )
            calls_after = int(getattr(self._generator, "_api_calls", calls_before))
            usage = list(getattr(self._generator, "api_usage_records", []))[
                usage_before:
            ]
            calls_this_arm = max(0, calls_after - calls_before)
            action = str(gemini_bundle.get("action", "unknown"))
            live_response = calls_this_arm > 0 and action in {
                "candidate-repair",
                "rejected-oracle-weakening",
            }
            if live_response:
                backend = "gemini"
            elif calls_this_arm:
                backend = "deterministic-fallback-after-gemini-error"
            else:
                backend = "guardrail-no-model-call"
            base["arms"]["gemini"] = self._evaluate_repair_arm(
                arm="gemini",
                scenario=scenario,
                scenario_dir=scenario_dir,
                source_path=source_path,
                source_sha256=source_sha256,
                analysis=analysis,
                broken_code=broken_code,
                reference_code=reference_code,
                classification=classification,
                bundle=gemini_bundle,
                reference_mutation=reference_mutation,
                mutation_limit=mutation_limit,
                provenance={
                    "backend": backend,
                    "model": getattr(self._generator, "model", self.config.model),
                    "temperature": getattr(
                        self._generator, "temperature", self.config.temperature
                    ),
                    "seed": getattr(self._generator, "seed", self.config.seed),
                    "api_calls": calls_this_arm,
                    "api_usage_records": usage,
                    "live_model_response": live_response,
                    "provider_available": True,
                },
            )

        source_after = _sha256(source_path.read_text(encoding="utf-8"))
        base["source_sha256_after"] = source_after
        if source_after != source_sha256:
            base["status"] = "invalid"
            base["invalid_reasons"] = ["The source changed during repair evaluation"]
        else:
            base["status"] = "valid"
        _write_json(scenario_dir / "scenario_result.json", base)
        return base

    def _evaluate_repair_arm(
        self,
        *,
        arm: str,
        scenario: HealingScenario,
        scenario_dir: Path,
        source_path: Path,
        source_sha256: str,
        analysis: dict[str, Any],
        broken_code: str,
        reference_code: str,
        classification: dict[str, Any],
        bundle: dict[str, Any],
        reference_mutation: MutationReport,
        mutation_limit: int,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        arm_dir = scenario_dir / arm
        arm_dir.mkdir(exist_ok=False)
        after_code = str(bundle.get("test_code", broken_code))
        after_path = arm_dir / "test_candidate.py"
        after_path.write_text(after_code, encoding="utf-8")
        validation = validate_generated_test_code(
            after_code, source_path, analysis, minimum_target_coverage=0.0
        )
        oracle_retained = _oracle_matches_reference(after_code, reference_code)

        if validation.get("passed"):
            outcome = run_pytest(
                str(after_path),
                timeout_seconds=self.config.test_timeout_seconds,
                isolated=True,
            )
            arm_status = "completed"
        else:
            outcome = {
                "executed": False,
                "passed": False,
                "timed_out": False,
                "isolated": True,
                "return_code": None,
                "duration_seconds": 0.0,
                "output": "Candidate rejected by static validation before execution.",
                "summary": {},
            }
            arm_status = "static-rejected"

        mutation_payload: dict[str, Any] | None = None
        semantic_retention: dict[str, Any] = {
            "available": False,
            "retained": None,
            "reason": "The repaired candidate did not pass the clean source.",
        }
        if outcome.get("passed"):
            try:
                candidate_mutation = evaluate_mutations(
                    source_path=scenario.source,
                    test_targets=[
                        after_path.relative_to(self.config.repo_root).as_posix()
                    ],
                    project_root=self.config.repo_root,
                    timeout_seconds=self.config.mutation_timeout_seconds,
                    max_mutants=mutation_limit,
                )
                mutation_payload = candidate_mutation.to_dict()
                semantic_retention = _compare_mutation_outcomes(
                    reference_mutation,
                    candidate_mutation,
                    oracle_retained=oracle_retained,
                )
                _write_json(arm_dir / "mutation.json", mutation_payload)
            except (BaselineFailure, MutationEvaluationError, ValueError) as error:
                semantic_retention = {
                    "available": False,
                    "retained": None,
                    "reason": (
                        "Candidate mutation evaluation failed closed: "
                        f"{type(error).__name__}: {error}"
                    ),
                }

        changed = _sha256(after_code) != _sha256(broken_code)
        record = {
            "arm": arm,
            "available": True,
            "status": arm_status,
            "classification": classification,
            "action": bundle.get("action", "unknown"),
            "explanation": bundle.get("explanation", []),
            "before_sha256": _sha256(broken_code),
            "after_sha256": _sha256(after_code),
            "changed": changed,
            "source_sha256_before": source_sha256,
            "source_sha256_after": _sha256(source_path.read_text(encoding="utf-8")),
            "validation": validation,
            "outcome": outcome,
            "repair_success": (
                bool(outcome.get("passed") and changed)
                if scenario.expected_safe_to_heal
                else None
            ),
            "unsafe_heal": (
                bool(changed) if not scenario.expected_safe_to_heal else None
            ),
            "oracle_matches_human_reference": oracle_retained,
            "semantic_retention": semantic_retention,
            "mutation": mutation_payload,
            "provenance": provenance,
        }
        _write_json(arm_dir / "arm_result.json", record)
        return record


def load_healing_manifest(path: str | Path) -> tuple[HealingScenario, ...]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Healing manifest schema_version must be 1")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Healing manifest must contain a non-empty scenarios list")
    parsed = tuple(HealingScenario.from_dict(item) for item in scenarios)
    identifiers = [item.scenario_id for item in parsed]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Healing scenario IDs must be unique")
    return parsed


def build_healing_summary(
    config: HealingExperimentConfig,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive outcome-neutral readiness and separate empirical claim support."""
    valid = [item for item in results if item.get("status") == "valid"]
    valid_study = [
        item
        for item in valid
        if item.get("scenario", {}).get("role", "study") == "study"
    ]
    declared_study = [
        item
        for item in results
        if item.get("scenario", {}).get("role", "study") == "study"
    ]
    invalid_study = [item for item in declared_study if item.get("status") != "valid"]

    all_metrics = _metrics_for_results(valid)
    study_metrics = _metrics_for_results(valid_study)

    reasons: list[str] = []
    if len(declared_study) < config.minimum_study_scenarios:
        reasons.append(
            f"Only {len(declared_study)} study scenario(s); at least "
            f"{config.minimum_study_scenarios} are required by this protocol."
        )
    project_ids = {
        str(item.get("scenario", {}).get("project_id", ""))
        for item in declared_study
        if item.get("scenario", {}).get("project_id")
    }
    if len(project_ids) < config.minimum_study_projects:
        reasons.append(
            f"Only {len(project_ids)} study project(s); at least "
            f"{config.minimum_study_projects} are required by this protocol."
        )
    if invalid_study:
        reasons.append(
            f"{len(invalid_study)} study scenario(s) were invalid or incomplete."
        )
    positives = [
        item
        for item in declared_study
        if item.get("scenario", {}).get("expected_safe_to_heal") is True
    ]
    negatives = [
        item
        for item in declared_study
        if item.get("scenario", {}).get("expected_safe_to_heal") is False
    ]
    if not positives:
        reasons.append("The study has no labelled repairable positive scenarios.")
    if not negatives:
        reasons.append("The study has no protected product/mixed negative scenarios.")
    if not declared_study:
        reasons.append("The manifest contains demo scenarios only; demos are never thesis-ready.")

    if config.claim_llm_effect and declared_study:
        gemini_arms = [item.get("arms", {}).get("gemini", {}) for item in declared_study]
        if config.offline or any(
            not arm.get("available")
            or not arm.get("provenance", {}).get("provider_available")
            for arm in gemini_arms
        ):
            reasons.append(
                "A live Gemini arm was not available for every study scenario required by the LLM claim."
            )
        positive_live_responses = [
            item.get("arms", {})
            .get("gemini", {})
            .get("provenance", {})
            .get("live_model_response", False)
            for item in positives
            if item.get("status") == "valid"
        ]
        if positives and not any(positive_live_responses):
            reasons.append(
                "No repairable study scenario received a successful live Gemini response."
            )

    evidence_readiness = {
        "ready": not reasons,
        "reasons": reasons,
        "outcome_neutral": True,
        "note": (
            "Readiness measures protocol volume, coverage, completeness, and provenance; "
            "it does not turn unsuccessful outcomes into missing evidence."
        ),
        "declared_study_scenarios": len(declared_study),
        "valid_study_scenarios": len(valid_study),
        "study_projects": len(project_ids),
        "invalid_study_scenarios": len(invalid_study),
    }
    claim_support = _build_claim_support(
        config=config,
        evidence_readiness=evidence_readiness,
        study_results=valid_study,
        study_metrics=study_metrics,
    )
    return {
        "scenario_count": len(results),
        "valid_scenario_count": len(valid),
        "invalid_scenario_count": len(results) - len(valid),
        "all_scenario_metrics": all_metrics,
        "study_metrics": study_metrics,
        "evidence_readiness": evidence_readiness,
        "claim_support": claim_support,
    }


def _metrics_for_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    universe = [str(item["scenario"]["scenario_id"]) for item in results]
    expected = [
        str(item["scenario"]["scenario_id"])
        for item in results
        if item["scenario"].get("expected_safe_to_heal") is True
    ]
    predicted = [
        str(item["scenario"]["scenario_id"])
        for item in results
        if item.get("classification", {}).get("safe_to_heal") is True
    ]
    raw_classification = selection_metrics(predicted, expected, universe)
    classification = {
        "scenario_count": raw_classification["universe_count"],
        "expected_safe_count": raw_classification["relevant_count"],
        "predicted_safe_count": raw_classification["selected_count"],
        "true_positive": raw_classification["true_positive"],
        "false_positive": raw_classification["false_positive"],
        "false_negative": raw_classification["false_negative"],
        "true_negative": raw_classification["true_negative"],
        "precision": raw_classification["precision"],
        "recall": raw_classification["recall"],
        "f1": raw_classification["f1"],
    }
    return {
        "classification": classification,
        "arms": {
            arm: _arm_metrics(results, arm)
            for arm in ("no_heal", "deterministic", "gemini")
        },
    }


def _arm_metrics(results: list[dict[str, Any]], arm_name: str) -> dict[str, Any]:
    positives = [
        item
        for item in results
        if item.get("scenario", {}).get("expected_safe_to_heal") is True
    ]
    negatives = [
        item
        for item in results
        if item.get("scenario", {}).get("expected_safe_to_heal") is False
    ]
    arms = [item.get("arms", {}).get(arm_name, {}) for item in results]
    available = [arm for arm in arms if arm.get("available")]
    positive_arms = [
        item.get("arms", {}).get(arm_name, {})
        for item in positives
        if item.get("arms", {}).get(arm_name, {}).get("available")
    ]
    negative_arms = [
        item.get("arms", {}).get(arm_name, {})
        for item in negatives
        if item.get("arms", {}).get(arm_name, {}).get("available")
    ]
    repair_successes = sum(arm.get("repair_success") is True for arm in positive_arms)
    unsafe_heals = sum(arm.get("unsafe_heal") is True for arm in negative_arms)
    retention = [
        arm.get("semantic_retention", {}).get("retained")
        for arm in positive_arms
        if arm.get("semantic_retention", {}).get("available")
    ]
    oracle_violations = sum(
        arm.get("repair_success") is True
        and arm.get("oracle_matches_human_reference") is not True
        for arm in positive_arms
    )
    durations = [
        float(arm.get("outcome", {}).get("duration_seconds", 0.0))
        for arm in available
        if arm.get("outcome", {}).get("executed", True)
    ]
    return {
        "available_scenarios": len(available),
        "positive_scenarios": len(positives),
        "evaluated_positive_scenarios": len(positive_arms),
        "repair_successes": repair_successes,
        "repair_success_rate_all_positives": _optional_ratio(
            repair_successes, len(positives)
        ),
        "negative_scenarios": len(negatives),
        "evaluated_negative_scenarios": len(negative_arms),
        "unsafe_heals": unsafe_heals,
        "unsafe_heal_rate_evaluated_negatives": _optional_ratio(
            unsafe_heals, len(negative_arms)
        ),
        "oracle_violation_count_among_successes": oracle_violations,
        "semantic_retention_evaluated": len(retention),
        "semantic_retention_successes": sum(value is True for value in retention),
        "semantic_retention_rate": _optional_ratio(
            sum(value is True for value in retention), len(retention)
        ),
        "isolated_execution_seconds": descriptive_statistics(durations),
    }


def _build_claim_support(
    *,
    config: HealingExperimentConfig,
    evidence_readiness: dict[str, Any],
    study_results: list[dict[str, Any]],
    study_metrics: dict[str, Any],
) -> dict[str, Any]:
    claim = (
        "The constrained live-Gemini healer improves repair recovery over no healing "
        "without unsafe edits or loss of the measured reference semantics."
    )
    if not config.claim_llm_effect:
        return {
            "claim": claim,
            "assessed": False,
            "supported": False,
            "reasons": ["An LLM-effect claim was not requested for this run."],
        }

    reasons: list[str] = []
    if not evidence_readiness.get("ready"):
        reasons.append("The LLM claim lacks a complete, thesis-scale evidence set.")
    proposed = study_metrics["arms"]["gemini"]
    control = study_metrics["arms"]["no_heal"]
    proposed_success = proposed.get("repair_success_rate_all_positives")
    control_success = control.get("repair_success_rate_all_positives")
    if (
        proposed_success is None
        or control_success is None
        or proposed_success <= control_success
    ):
        reasons.append("Gemini repair success did not exceed the no-heal control.")
    if proposed.get("evaluated_negative_scenarios") != proposed.get(
        "negative_scenarios"
    ):
        reasons.append("Not every protected negative received a Gemini-arm verdict.")
    if proposed.get("unsafe_heals", 0):
        reasons.append("At least one protected negative was modified by the healer.")
    if proposed.get("oracle_violation_count_among_successes", 0):
        reasons.append("At least one successful repair diverged from the human oracle.")
    if proposed.get("semantic_retention_evaluated", 0) and proposed.get(
        "semantic_retention_rate"
    ) != 1.0:
        reasons.append("Mutation-outcome semantics were not fully retained.")
    if study_metrics["classification"].get("false_positive", 0):
        reasons.append("The repairability classifier produced an unsafe false positive.")

    successful_live = any(
        item.get("arms", {})
        .get("gemini", {})
        .get("provenance", {})
        .get("live_model_response")
        for item in study_results
        if item.get("scenario", {}).get("expected_safe_to_heal") is True
    )
    if not successful_live:
        reasons.append("No positive study scenario has a verified live Gemini response.")
    return {
        "claim": claim,
        "assessed": bool(evidence_readiness.get("ready")),
        "supported": not reasons,
        "reasons": reasons,
        "decision_rule": (
            "Evidence ready; live Gemini repair rate exceeds no-heal; no unsafe edits, "
            "oracle violations, measured semantic-retention failures, or classifier false positives."
        ),
    }


def _control_arm_record(
    *,
    broken_code: str,
    reference_code: str,
    classification: dict[str, Any],
    validation: dict[str, Any],
    outcome: dict[str, Any],
    expected_safe_to_heal: bool,
    source_sha256: str,
) -> dict[str, Any]:
    oracle_retained = _oracle_matches_reference(broken_code, reference_code)
    return {
        "arm": "no_heal",
        "available": True,
        "status": "completed",
        "classification": classification,
        "action": "no-heal-control",
        "before_sha256": _sha256(broken_code),
        "after_sha256": _sha256(broken_code),
        "changed": False,
        "source_sha256_before": source_sha256,
        "source_sha256_after": source_sha256,
        "validation": validation,
        "outcome": outcome,
        "repair_success": False if expected_safe_to_heal else None,
        "unsafe_heal": False if not expected_safe_to_heal else None,
        "oracle_matches_human_reference": oracle_retained,
        "semantic_retention": {
            "available": False,
            "retained": None,
            "reason": "No-heal control retained the intentionally broken input.",
        },
        "mutation": None,
        "provenance": {
            "backend": "none",
            "model": None,
            "api_calls": 0,
            "live_model_response": False,
            "provider_available": True,
        },
    }


def _unavailable_gemini_arm(
    config: HealingExperimentConfig,
    classification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "arm": "gemini",
        "available": False,
        "status": "unavailable",
        "classification": classification,
        "action": "not-run",
        "before_sha256": None,
        "after_sha256": None,
        "changed": None,
        "validation": None,
        "outcome": {
            "executed": False,
            "passed": False,
            "reason": "Offline mode or live Gemini provider unavailable.",
        },
        "repair_success": None,
        "unsafe_heal": None,
        "oracle_matches_human_reference": None,
        "semantic_retention": {"available": False, "retained": None},
        "mutation": None,
        "provenance": {
            "backend": "unavailable",
            "model": config.model,
            "temperature": config.temperature,
            "seed": config.seed,
            "api_calls": 0,
            "live_model_response": False,
            "provider_available": False,
        },
    }


def _oracle_matches_reference(candidate: str, reference: str) -> bool:
    """Use the existing guard in both directions to require equal oracle sets."""
    try:
        return preserves_oracle_strength(reference, candidate) and preserves_oracle_strength(
            candidate, reference
        )
    except Exception:
        return False


def _compare_mutation_outcomes(
    reference: MutationReport,
    candidate: MutationReport,
    *,
    oracle_retained: bool,
) -> dict[str, Any]:
    reference_status = {item.mutant_id: item.status for item in reference.mutants}
    candidate_status = {item.mutant_id: item.status for item in candidate.mutants}
    if not reference_status:
        return {
            "available": False,
            "retained": None,
            "reason": "The source produced no deterministic mutants.",
            "oracle_matches_human_reference": oracle_retained,
        }
    ids_match = set(reference_status) == set(candidate_status)
    common = sorted(set(reference_status) & set(candidate_status))
    agreements = sum(reference_status[item] == candidate_status[item] for item in common)
    deterministic_statuses = {"killed", "survived"}
    fully_decidable = all(
        reference_status[item] in deterministic_statuses
        and candidate_status[item] in deterministic_statuses
        for item in common
    )
    exact = ids_match and len(common) == len(reference_status) and agreements == len(common)
    killed_reference = set(reference.killed_ids)
    killed_by_candidate = set(candidate.killed_ids) & killed_reference
    fault_retention = fault_detection_metrics(killed_by_candidate, killed_reference)
    retained = bool(oracle_retained and exact and fully_decidable)
    return {
        "available": True,
        "retained": retained,
        "oracle_matches_human_reference": oracle_retained,
        "mutant_ids_match": ids_match,
        "mutant_count": len(reference_status),
        "outcome_agreements": agreements,
        "outcome_agreement_rate": round(agreements / len(common), 4)
        if common
        else 0.0,
        "all_mutants_received_deterministic_verdicts": fully_decidable,
        "reference_fault_retention": fault_retention,
    }


def _write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "scenario_id",
        "project_id",
        "role",
        "expected_safe_to_heal",
        "scenario_status",
        "arm",
        "arm_available",
        "action",
        "changed",
        "passed",
        "repair_success",
        "unsafe_heal",
        "oracle_matches_human_reference",
        "semantic_retained",
        "backend",
        "api_calls",
        "before_sha256",
        "after_sha256",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            scenario = result.get("scenario", {})
            for name, arm in result.get("arms", {}).items():
                writer.writerow(
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "project_id": scenario.get("project_id"),
                        "role": scenario.get("role"),
                        "expected_safe_to_heal": scenario.get(
                            "expected_safe_to_heal"
                        ),
                        "scenario_status": result.get("status"),
                        "arm": name,
                        "arm_available": arm.get("available"),
                        "action": arm.get("action"),
                        "changed": arm.get("changed"),
                        "passed": arm.get("outcome", {}).get("passed"),
                        "repair_success": arm.get("repair_success"),
                        "unsafe_heal": arm.get("unsafe_heal"),
                        "oracle_matches_human_reference": arm.get(
                            "oracle_matches_human_reference"
                        ),
                        "semantic_retained": arm.get("semantic_retention", {}).get(
                            "retained"
                        ),
                        "backend": arm.get("provenance", {}).get("backend"),
                        "api_calls": arm.get("provenance", {}).get("api_calls"),
                        "before_sha256": arm.get("before_sha256"),
                        "after_sha256": arm.get("after_sha256"),
                    }
                )


def _write_markdown_report(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    readiness = summary["evidence_readiness"]
    claim = summary["claim_support"]
    study = summary["study_metrics"]
    lines = [
        "# Controlled Self-Healing Experiment",
        "",
        f"Experiment ID: `{payload['experiment_id']}`",
        "",
        "## Evidence readiness",
        "",
        (
            "The protocol evidence is ready for interpretation."
            if readiness["ready"]
            else "This run is **not** thesis-ready."
        ),
        "",
    ]
    lines.extend(f"- {reason}" for reason in readiness["reasons"])
    lines.extend(
        [
            "",
            "Readiness is outcome-neutral; negative results remain valid evidence.",
            "",
            "## Study classification metrics",
            "",
            f"- Precision: `{study['classification']['precision']}`",
            f"- Recall: `{study['classification']['recall']}`",
            f"- F1: `{study['classification']['f1']}`",
            "",
            "## Arm outcomes",
            "",
            "| Arm | Repair success | Unsafe-heal rate | Semantic retention |",
            "|---|---:|---:|---:|",
        ]
    )
    for name in ("no_heal", "deterministic", "gemini"):
        metrics = study["arms"][name]
        lines.append(
            f"| {name} | {_fmt(metrics['repair_success_rate_all_positives'])} | "
            f"{_fmt(metrics['unsafe_heal_rate_evaluated_negatives'])} | "
            f"{_fmt(metrics['semantic_retention_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Claim support",
            "",
            f"- Assessed: `{claim['assessed']}`",
            f"- Supported by this run: `{claim['supported']}`",
        ]
    )
    lines.extend(f"- {reason}" for reason in claim["reasons"])
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- Unit-level oracle fingerprints are supplemented by comparison with a human-corrected test and deterministic mutant outcomes; this is measured retention, not a proof of semantic equivalence.",
            "- Demo scenarios are harness checks and can never satisfy thesis readiness.",
            "- Raw inputs, candidates, execution output, hashes, mutations, actions, and provider provenance are retained under `raw/`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _config_payload(config: HealingExperimentConfig) -> dict[str, Any]:
    return {
        "scenarios": [asdict(item) for item in config.scenarios],
        "offline": config.offline,
        "model": config.model,
        "temperature": config.temperature,
        "seed": config.seed,
        "test_timeout_seconds": config.test_timeout_seconds,
        "mutation_timeout_seconds": config.mutation_timeout_seconds,
        "mutation_limit_override": config.mutation_limit_override,
        "minimum_study_scenarios": config.minimum_study_scenarios,
        "minimum_study_projects": config.minimum_study_projects,
        "claim_llm_effect": config.claim_llm_effect,
    }


def _collect_provenance(config: HealingExperimentConfig) -> dict[str, Any]:
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_output(config.repo_root, ["rev-parse", "HEAD"]) or None,
        "git_dirty": bool(_git_output(config.repo_root, ["status", "--porcelain"])),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "model": config.model,
        "temperature": config.temperature,
        "seed": config.seed,
        "offline_requested": config.offline,
        "claim_llm_effect_requested": config.claim_llm_effect,
    }


def _git_output(root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {
                    "COMSPEC",
                    "PATH",
                    "PATHEXT",
                    "SYSTEMDRIVE",
                    "SYSTEMROOT",
                    "WINDIR",
                }
            },
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _resolve_output_directory(root: Path, output_root: Path, run_id: str) -> Path:
    base = output_root if output_root.is_absolute() else root / output_root
    resolved = base.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("Healing output_root must remain inside the repository") from error
    return resolved / _safe_name(run_id)


def _require_relative_file(root: Path, value: str, label: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise ValueError(f"{label} path must be repository-relative: {value}")
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} path must stay inside repository: {value}") from error
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {value}")
    return path


def _safe_name(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    if not cleaned:
        raise ValueError("Identifier must contain at least one safe character")
    return cleaned


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _fmt(value: Any) -> str:
    return "N/A" if value is None else str(value)


__all__ = [
    "HealingExperimentConfig",
    "HealingExperimentRunner",
    "HealingScenario",
    "build_healing_summary",
    "load_healing_manifest",
]
