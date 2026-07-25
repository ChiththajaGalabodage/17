"""Evidence-preserving research experiment orchestration.

The runner evaluates generated tests and a manually authored reference suite
against the same clean source and the same deterministic mutant IDs.  It never
uses failed pytest cases as a proxy for unique defects, never hardcodes result
totals, and keeps every raw run in a unique directory.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from src.mutation_testing import BaselineFailure, MutationReport, evaluate_mutations
from src.coverage_evaluation import evaluate_coverage
from src.research_metrics import (
    descriptive_statistics,
    fault_detection_metrics,
    paired_comparison,
)
from src.runner import run_stability


@dataclass(frozen=True, slots=True)
class SubjectConfig:
    """One clean project subject and its manually authored oracle suite."""

    subject_id: str
    source: str
    manual_tests: tuple[str, ...]
    role: str = "study"
    minimum_target_coverage: float = 50.0
    project_id: str | None = None
    revision: str | None = None
    source_sha256: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubjectConfig":
        tests = payload.get("manual_tests", [])
        if not isinstance(tests, list) or not tests:
            raise ValueError("Each subject requires a non-empty manual_tests list")
        return cls(
            subject_id=str(payload["id"]),
            source=str(payload["source"]),
            manual_tests=tuple(str(item) for item in tests),
            role=str(payload.get("role", "study")),
            minimum_target_coverage=float(payload.get("minimum_target_coverage", 50.0)),
            project_id=(str(payload["project_id"]) if payload.get("project_id") else None),
            revision=(str(payload["revision"]) if payload.get("revision") else None),
            source_sha256=(
                str(payload["source_sha256"]).lower()
                if payload.get("source_sha256")
                else None
            ),
        )


@dataclass(slots=True)
class ExperimentConfig:
    """Immutable-input settings for a complete experiment run."""

    repo_root: Path
    subjects: tuple[SubjectConfig, ...]
    runs: int = 3
    model: str = "gemini-2.5-flash"
    temperature: float = 0.2
    base_seed: int = 4885
    offline: bool = False
    stability_runs: int = 3
    mutation_limit: int = 20
    mutation_timeout_seconds: float = 30.0
    pipeline_timeout_seconds: float = 300.0
    max_heal_attempts: int = 2
    output_root: Path = Path("reports/research_runs")
    run_id: str | None = None
    minimum_study_subjects: int = 3
    minimum_study_projects: int = 3
    minimum_killable_faults: int = 30
    minimum_generation_runs: int = 3
    minimum_stability_runs: int = 3
    equivalent_mutant_protocol: Path | None = None
    allow_uncontained_llm_tests: bool = False

    def validate(self) -> None:
        self.repo_root = self.repo_root.resolve()
        if not self.repo_root.is_dir():
            raise FileNotFoundError(f"Repository root not found: {self.repo_root}")
        if not self.subjects:
            raise ValueError("At least one subject is required")
        if self.runs <= 0 or self.stability_runs <= 0 or self.mutation_limit <= 0:
            raise ValueError("runs, stability_runs, and mutation_limit must be positive")
        if self.mutation_timeout_seconds <= 0 or self.pipeline_timeout_seconds <= 0:
            raise ValueError("Experiment timeouts must be positive")
        if self.equivalent_mutant_protocol is not None:
            protocol = self.equivalent_mutant_protocol
            if not protocol.is_absolute():
                protocol = self.repo_root / protocol
            protocol = protocol.resolve()
            try:
                protocol.relative_to(self.repo_root)
            except ValueError as error:
                raise ValueError(
                    "equivalent_mutant_protocol must stay inside the repository"
                ) from error
            if not protocol.is_file():
                raise FileNotFoundError(
                    f"Equivalent-mutant protocol not found: {protocol}"
                )
            self.equivalent_mutant_protocol = protocol
        ids = [subject.subject_id for subject in self.subjects]
        if len(ids) != len(set(ids)):
            raise ValueError("Subject IDs must be unique")
        for subject in self.subjects:
            if subject.role not in {"study", "demo"}:
                raise ValueError("Subject role must be 'study' or 'demo'")
            _require_relative_existing(self.repo_root, subject.source, "source")
            for test_target in subject.manual_tests:
                _require_relative_existing(
                    self.repo_root,
                    test_target.partition("::")[0],
                    "manual test",
                )


class ExperimentRunner:
    """Run isolated generation/mutation comparisons and derive one report."""

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Any]:
        self.config.validate()
        run_id = self.config.run_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        experiment_dir = _resolve_output_directory(
            self.config.repo_root,
            self.config.output_root,
            run_id,
        )
        experiment_dir.mkdir(parents=True, exist_ok=False)

        provenance = _collect_provenance(self.config)
        _write_json(experiment_dir / "provenance.json", provenance)

        subject_results = [
            self._run_subject(subject, experiment_dir)
            for subject in self.config.subjects
        ]
        summary = _summarize_experiment(self.config, subject_results)
        payload = {
            "schema_version": 3,
            "experiment_id": run_id,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiment_directory": _relative_or_absolute(
                experiment_dir, self.config.repo_root
            ),
            "config": _config_payload(self.config),
            "provenance": provenance,
            "subjects": subject_results,
            "summary": summary,
        }
        _write_json(experiment_dir / "summary.json", payload)
        _write_results_csv(experiment_dir / "results.csv", subject_results)
        _write_markdown_report(experiment_dir / "research_report.md", payload)
        return payload

    def _run_subject(
        self,
        subject: SubjectConfig,
        experiment_dir: Path,
    ) -> dict[str, Any]:
        subject_dir = experiment_dir / "raw" / _safe_name(subject.subject_id)
        subject_dir.mkdir(parents=True, exist_ok=False)
        observed_source_sha256 = hashlib.sha256(
            (self.config.repo_root / subject.source).read_bytes()
        ).hexdigest()
        manual_stability = run_stability(
            list(subject.manual_tests),
            runs=self.config.stability_runs,
            isolated=True,
            timeout_seconds=self.config.mutation_timeout_seconds,
        )
        result: dict[str, Any] = {
            "subject": asdict(subject),
            "observed_source_sha256": observed_source_sha256,
            "status": "running",
            "manual_stability": manual_stability,
            "reference_mutation": None,
            "reference_coverage": None,
            "runs": [],
        }

        if not manual_stability.get("consistent") or not manual_stability.get(
            "all_passed"
        ):
            result["status"] = "invalid-reference-stability"
            result["reason"] = (
                "The manual reference suite must pass consistently before it can "
                "serve as an experiment oracle."
            )
            _write_json(subject_dir / "subject_result.json", result)
            return result

        try:
            reference_report = evaluate_mutations(
                source_path=subject.source,
                test_targets=subject.manual_tests,
                project_root=self.config.repo_root,
                timeout_seconds=self.config.mutation_timeout_seconds,
                max_mutants=self.config.mutation_limit,
            )
        except BaselineFailure as error:
            result["status"] = "invalid-reference-baseline"
            result["reference_baseline_failure"] = asdict(error.result)
            result["reason"] = (
                "The manual reference suite must pass on the clean source before "
                "fault-detection metrics are valid."
            )
            _write_json(subject_dir / "subject_result.json", result)
            return result

        reference_payload = reference_report.to_dict()
        result["reference_mutation"] = reference_payload
        _write_json(subject_dir / "reference_mutation.json", reference_payload)
        reference_coverage = evaluate_coverage(
            subject.source,
            subject.manual_tests,
            project_root=self.config.repo_root,
            timeout_seconds=self.config.mutation_timeout_seconds,
        )
        result["reference_coverage"] = reference_coverage
        _write_json(subject_dir / "reference_coverage.json", reference_coverage)
        if not reference_coverage.get("valid") or not reference_coverage.get(
            "tests_passed"
        ):
            result["status"] = "invalid-reference-coverage"
            result["reason"] = (
                "Reference coverage execution must be valid and pass on the clean source."
            )
            _write_json(subject_dir / "subject_result.json", result)
            return result
        killable_fault_ids = set(reference_report.killed_ids)

        for run_index in range(1, self.config.runs + 1):
            run_result = self._run_generated_suite(
                subject=subject,
                subject_dir=subject_dir,
                run_index=run_index,
                killable_fault_ids=killable_fault_ids,
            )
            result["runs"].append(run_result)

        result["status"] = "completed"
        result["summary"] = _summarize_subject(result)
        _write_json(subject_dir / "subject_result.json", result)
        return result

    def _run_generated_suite(
        self,
        *,
        subject: SubjectConfig,
        subject_dir: Path,
        run_index: int,
        killable_fault_ids: set[str],
    ) -> dict[str, Any]:
        run_dir = subject_dir / f"run_{run_index:03d}"
        run_dir.mkdir(parents=True, exist_ok=False)
        generated_path = run_dir / "generated_tests.py"
        pipeline_report_path = run_dir / "pipeline.json"
        generated_relative = generated_path.relative_to(self.config.repo_root).as_posix()
        report_relative = pipeline_report_path.relative_to(self.config.repo_root).as_posix()

        command = [
            sys.executable,
            "main.py",
            "--source",
            subject.source,
            "--test-output",
            generated_relative,
            "--report-output",
            report_relative,
            "--model",
            self.config.model,
            "--temperature",
            str(self.config.temperature),
            "--seed",
            str(self.config.base_seed + run_index - 1),
            "--max-heal-attempts",
            str(self.config.max_heal_attempts),
            "--stability-runs",
            str(self.config.stability_runs),
            "--minimum-target-coverage",
            str(subject.minimum_target_coverage),
            "--test-timeout",
            str(self.config.mutation_timeout_seconds),
        ]
        if self.config.offline:
            command.append("--offline")
        if self.config.allow_uncontained_llm_tests:
            command.append("--allow-uncontained-llm-tests")
        process = _run_command(
            command,
            cwd=self.config.repo_root,
            timeout_seconds=self.config.pipeline_timeout_seconds,
        )
        (run_dir / "pipeline.stdout.txt").write_text(
            process["stdout"], encoding="utf-8"
        )
        (run_dir / "pipeline.stderr.txt").write_text(
            process["stderr"], encoding="utf-8"
        )

        pipeline = _read_json(pipeline_report_path)
        record: dict[str, Any] = {
            "run": run_index,
            "process": {
                "return_code": process["return_code"],
                "timed_out": process["timed_out"],
                "duration_seconds": process["duration_seconds"],
            },
            "pipeline_report": report_relative,
            "generated_test": generated_relative,
            "pipeline_report_present": bool(pipeline),
            "mutation": None,
            "coverage": None,
            "fault_detection": None,
        }
        if not pipeline:
            record["status"] = "missing-pipeline-report"
            _write_json(run_dir / "run_record.json", record)
            return record

        semantic_passed = pipeline.get("semantic_status") == "PASSED"
        expected_exit = 0 if semantic_passed else int(
            pipeline.get("test_run", {}).get("return_code", 1) or 1
        )
        record.update(
            {
                "semantic_status": pipeline.get("semantic_status"),
                "exit_status_consistent": (
                    (process["return_code"] == 0) == semantic_passed
                ),
                "validation": pipeline.get("validation", {}),
                "stability": pipeline.get("stability", {}),
                "generation_provenance": pipeline.get(
                    "generation_provenance", {}
                ),
                "pipeline_duration_seconds": pipeline.get(
                    "pipeline_duration_seconds"
                ),
                "test_execution_seconds": pipeline.get("test_run", {}).get(
                    "duration_seconds"
                ),
                "expected_semantic_exit": expected_exit,
            }
        )
        candidate_valid = bool(
            semantic_passed
            and pipeline.get("validation", {}).get("passed")
            and pipeline.get("stability", {}).get("consistent")
            and generated_path.is_file()
        )
        if not candidate_valid:
            record["status"] = "invalid-generated-suite"
            _write_json(run_dir / "run_record.json", record)
            return record

        try:
            mutation_report = evaluate_mutations(
                source_path=subject.source,
                test_targets=[generated_relative],
                project_root=self.config.repo_root,
                timeout_seconds=self.config.mutation_timeout_seconds,
                max_mutants=self.config.mutation_limit,
            )
        except BaselineFailure as error:
            record["status"] = "generated-suite-isolation-baseline-failed"
            record["mutation_baseline_failure"] = asdict(error.result)
            _write_json(run_dir / "run_record.json", record)
            return record

        mutation_payload = mutation_report.to_dict()
        mutation_path = run_dir / "mutation.json"
        _write_json(mutation_path, mutation_payload)
        killed_reference_faults = set(mutation_report.killed_ids) & killable_fault_ids
        fault_metrics = fault_detection_metrics(
            killed_reference_faults,
            killable_fault_ids,
        )
        coverage_payload = evaluate_coverage(
            subject.source,
            [generated_relative],
            project_root=self.config.repo_root,
            timeout_seconds=self.config.mutation_timeout_seconds,
        )
        coverage_path = run_dir / "coverage.json"
        _write_json(coverage_path, coverage_payload)
        record.update(
            {
                "status": "valid",
                "mutation": mutation_payload,
                "coverage": coverage_payload,
                "coverage_report": coverage_path.relative_to(
                    self.config.repo_root
                ).as_posix(),
                "mutation_report": mutation_path.relative_to(
                    self.config.repo_root
                ).as_posix(),
                "fault_detection": fault_metrics,
            }
        )
        _write_json(run_dir / "run_record.json", record)
        return record


def load_subject_manifest(path: str | Path) -> tuple[SubjectConfig, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Subject manifest schema_version must be 1")
    subjects = payload.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("Subject manifest must contain a non-empty subjects list")
    return tuple(SubjectConfig.from_dict(item) for item in subjects)


def _summarize_subject(subject_result: dict[str, Any]) -> dict[str, Any]:
    runs = subject_result.get("runs", [])
    valid_runs = [item for item in runs if item.get("status") == "valid"]
    mutation_scores = [
        float(item["mutation"]["mutation_score"]) for item in valid_runs
    ]
    fault_recalls = [
        float(item["fault_detection"]["fault_recall"]) * 100.0
        for item in valid_runs
    ]
    target_coverages = [
        float(
            item.get("validation", {})
            .get("metrics", {})
            .get("target_function_coverage_percent", 0.0)
        )
        for item in valid_runs
    ]
    execution_times = [
        float(item["mutation"]["baseline"]["duration_seconds"])
        for item in valid_runs
    ]
    pipeline_times = [
        float(item.get("pipeline_duration_seconds", 0.0) or 0.0)
        for item in valid_runs
    ]
    line_coverages = [
        float(item.get("coverage", {}).get("line_coverage_percent", 0.0))
        for item in valid_runs
        if item.get("coverage", {}).get("valid")
    ]
    branch_coverages = [
        float(item.get("coverage", {}).get("branch_coverage_percent", 0.0))
        for item in valid_runs
        if item.get("coverage", {}).get("valid")
    ]
    backends = Counter(
        str(item.get("generation_provenance", {}).get("backend", "unknown"))
        for item in runs
    )
    hashes = {
        item.get("generation_provenance", {}).get("generated_test_sha256")
        for item in runs
        if item.get("generation_provenance", {}).get("generated_test_sha256")
    }
    return {
        "requested_runs": len(runs),
        "valid_runs": len(valid_runs),
        "valid_generated_suite_rate": round(
            len(valid_runs) / len(runs) if runs else 0.0, 4
        ),
        "generation_backends": dict(sorted(backends.items())),
        "unique_source_hashes": len(hashes),
        "mutation_score_percent": descriptive_statistics(mutation_scores),
        "reference_fault_recall_percent": descriptive_statistics(fault_recalls),
        "target_callable_coverage_percent": descriptive_statistics(target_coverages),
        "line_coverage_percent": descriptive_statistics(line_coverages),
        "branch_coverage_percent": descriptive_statistics(branch_coverages),
        "isolated_test_execution_seconds": descriptive_statistics(execution_times),
        "end_to_end_pipeline_seconds": descriptive_statistics(pipeline_times),
    }


def _summarize_experiment(
    config: ExperimentConfig,
    subject_results: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [
        item for item in subject_results if item.get("status") == "completed"
    ]
    configured_study_subjects = [
        subject for subject in config.subjects if subject.role == "study"
    ]
    study_subjects = [
        item
        for item in completed
        if item.get("subject", {}).get("role", "study") == "study"
    ]
    killable_faults = {
        f"{item.get('observed_source_sha256')}::{fault_id}"
        for item in study_subjects
        for fault_id in (
            item.get("reference_mutation", {}).get("killed_mutant_ids", [])
        )
    }
    fallback_runs = sum(
        1
        for item in study_subjects
        for run in item.get("runs", [])
        if run.get("generation_provenance", {}).get("backend") != "gemini"
    )
    invalid_runs = sum(
        1
        for item in study_subjects
        for run in item.get("runs", [])
        if run.get("status") != "valid"
    )

    reasons: list[str] = []
    if len(study_subjects) < config.minimum_study_subjects:
        reasons.append(
            f"Only {len(study_subjects)} study subject(s); at least "
            f"{config.minimum_study_subjects} are required by this protocol."
        )
    if len(killable_faults) < config.minimum_killable_faults:
        reasons.append(
            f"Only {len(killable_faults)} unique killable study mutants; at least "
            f"{config.minimum_killable_faults} are required by this protocol."
        )
    if len(study_subjects) != len(configured_study_subjects):
        reasons.append(
            f"Only {len(study_subjects)} of {len(configured_study_subjects)} configured study subject(s) "
            "had stable, passing, coverage-valid reference suites."
        )
    project_ids = {
        item.get("subject", {}).get("project_id")
        for item in study_subjects
        if item.get("subject", {}).get("project_id")
    }
    if len(project_ids) < config.minimum_study_projects:
        reasons.append(
            f"Only {len(project_ids)} unique pinned study project(s); at least "
            f"{config.minimum_study_projects} are required by this protocol."
        )
    source_hashes = [
        str(item.get("observed_source_sha256")) for item in study_subjects
    ]
    if len(source_hashes) != len(set(source_hashes)):
        reasons.append(
            "Duplicate study source hashes were found; duplicated subjects cannot inflate evidence volume."
        )
    unpinned = [
        str(item.get("subject", {}).get("subject_id"))
        for item in study_subjects
        if not item.get("subject", {}).get("project_id")
        or not item.get("subject", {}).get("revision")
        or not item.get("subject", {}).get("source_sha256")
    ]
    if unpinned:
        reasons.append(
            "Study subjects missing project_id, revision, or source_sha256: "
            + ", ".join(sorted(unpinned))
            + "."
        )
    mismatched_hashes = [
        str(item.get("subject", {}).get("subject_id"))
        for item in study_subjects
        if item.get("subject", {}).get("source_sha256")
        and str(item.get("subject", {}).get("source_sha256")).lower()
        != str(item.get("observed_source_sha256")).lower()
    ]
    if mismatched_hashes:
        reasons.append(
            "Declared source_sha256 did not match observed content for: "
            + ", ".join(sorted(mismatched_hashes))
            + "."
        )
    if config.runs < config.minimum_generation_runs:
        reasons.append(
            f"Only {config.runs} generation run(s) per subject; at least "
            f"{config.minimum_generation_runs} are required by this protocol."
        )
    if config.stability_runs < config.minimum_stability_runs:
        reasons.append(
            f"Only {config.stability_runs} stability run(s); at least "
            f"{config.minimum_stability_runs} are required by this protocol."
        )
    if config.equivalent_mutant_protocol is None:
        reasons.append(
            "No versioned equivalent-mutant review protocol was supplied."
        )
    if fallback_runs:
        reasons.append(
            f"{fallback_runs} study run(s) used a non-LLM fallback and cannot support an LLM-effect claim."
        )
    if invalid_runs:
        reasons.append(f"{invalid_runs} study run(s) failed the generated-suite quality gates.")
    if not study_subjects:
        reasons.append("The manifest contains only demo subjects, not thesis study subjects.")
    if study_subjects and _git_output(config.repo_root, ["status", "--porcelain"]):
        reasons.append(
            "The study worktree is dirty; freeze each subject/change in an immutable clean revision."
        )

    proposed_by_subject: list[float] = []
    reference_by_subject: list[float] = []
    paired_subject_ids: list[str] = []
    for item in study_subjects:
        valid_scores = [
            float(run["mutation"]["mutation_score"])
            for run in item.get("runs", [])
            if run.get("status") == "valid"
        ]
        reference = item.get("reference_mutation", {}).get("mutation_score")
        if valid_scores and reference is not None:
            proposed_by_subject.append(mean(valid_scores))
            reference_by_subject.append(float(reference))
            paired_subject_ids.append(item["subject"]["subject_id"])

    paired = None
    if proposed_by_subject:
        paired = paired_comparison(
            proposed_by_subject,
            reference_by_subject,
            higher_is_better=True,
        )
        paired["paired_subject_ids"] = paired_subject_ids

    generation_evidence_readiness = {
        "ready": not reasons,
        "scope": "test-generation-and-fault-detection",
        "reasons": reasons,
        "note": (
            "Evidence readiness checks protocol completeness and provenance; "
            "it does not assert superiority."
        ),
    }
    self_healing_evidence_readiness = {
        "ready": False,
        "scope": "self-healing",
        "reasons": [
            "No controlled, labeled self-healing scenarios are evaluated by this experiment."
        ],
        "note": "Use the dedicated healing experiment before making an effectiveness or safety claim.",
    }
    overall_reasons = list(reasons)
    overall_reasons.extend(
        [
            "Selection evidence must be supplied by the separate selection experiment.",
            *self_healing_evidence_readiness["reasons"],
        ]
    )

    return {
        "completed_subjects": len(completed),
        "study_subjects": len(study_subjects),
        "configured_study_subjects": len(configured_study_subjects),
        "unique_study_projects": len(project_ids),
        "unique_study_source_hashes": len(set(source_hashes)),
        "unique_killable_study_mutants": len(killable_faults),
        "fallback_study_runs": fallback_runs,
        "invalid_study_runs": invalid_runs,
        "mutation_score_paired_by_subject": paired,
        "evidence_readiness": generation_evidence_readiness,
        "claim_readiness": {
            **generation_evidence_readiness,
            "compatibility_note": (
                "Legacy field name; this is evidence readiness for the stated scope, not claim support."
            ),
        },
        "self_healing_evidence_readiness": self_healing_evidence_readiness,
        "overall_framework_evidence_readiness": {
            "ready": False,
            "scope": "generation-selection-healing",
            "reasons": overall_reasons,
            "note": "Combine outcome-neutral readiness from all component experiments.",
        },
        "claim_support": {
            "assessed": False,
            "supports_superiority": None,
            "reasons": [
                "Apply preregistered effect and uncertainty criteria to an evidence-ready study before claiming superiority."
            ],
        },
    }


def _write_results_csv(path: Path, subject_results: list[dict[str, Any]]) -> None:
    fields = [
        "subject_id",
        "subject_role",
        "run",
        "status",
        "backend",
        "semantic_status",
        "validation_passed",
        "flaky",
        "target_callable_coverage_percent",
        "assertion_count",
        "line_coverage_percent",
        "branch_coverage_percent",
        "mutation_score_percent",
        "reference_fault_recall_percent",
        "isolated_execution_seconds",
        "end_to_end_pipeline_seconds",
        "pipeline_report",
        "mutation_report",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for subject in subject_results:
            subject_payload = subject.get("subject", {})
            for run in subject.get("runs", []):
                metrics = run.get("validation", {}).get("metrics", {})
                mutation = run.get("mutation") or {}
                fault = run.get("fault_detection") or {}
                coverage = run.get("coverage") or {}
                writer.writerow(
                    {
                        "subject_id": subject_payload.get("subject_id"),
                        "subject_role": subject_payload.get("role"),
                        "run": run.get("run"),
                        "status": run.get("status"),
                        "backend": run.get("generation_provenance", {}).get("backend"),
                        "semantic_status": run.get("semantic_status"),
                        "validation_passed": run.get("validation", {}).get("passed"),
                        "flaky": run.get("stability", {}).get("flaky"),
                        "target_callable_coverage_percent": metrics.get(
                            "target_function_coverage_percent"
                        ),
                        "assertion_count": metrics.get("assertion_count"),
                        "line_coverage_percent": coverage.get("line_coverage_percent"),
                        "branch_coverage_percent": coverage.get("branch_coverage_percent"),
                        "mutation_score_percent": mutation.get("mutation_score"),
                        "reference_fault_recall_percent": (
                            float(fault["fault_recall"]) * 100.0
                            if "fault_recall" in fault
                            else None
                        ),
                        "isolated_execution_seconds": mutation.get("baseline", {}).get(
                            "duration_seconds"
                        ),
                        "end_to_end_pipeline_seconds": run.get(
                            "pipeline_duration_seconds"
                        ),
                        "pipeline_report": run.get("pipeline_report"),
                        "mutation_report": run.get("mutation_report"),
                    }
                )


def _write_markdown_report(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    readiness = summary["evidence_readiness"]
    lines = [
        "# Research Experiment Report",
        "",
        f"Experiment ID: `{payload['experiment_id']}`",
        "",
        "## Evidence status",
        "",
        (
            "This run meets the configured evidence-volume and provenance checks."
            if readiness["ready"]
            else "This run does **not** yet meet the configured thesis-evidence checks."
        ),
        "",
    ]
    if readiness["reasons"]:
        lines.extend(f"- {reason}" for reason in readiness["reasons"])
        lines.append("")

    lines.extend(
        [
            "## Subject results",
            "",
            "| Subject | Role | Ref mutation | Gen mutation mean | Fault recall mean | Ref line/branch | Gen line/branch mean | Valid runs | Backend(s) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload["subjects"]:
        subject = item.get("subject", {})
        reference = item.get("reference_mutation") or {}
        subject_summary = item.get("summary", {})
        mutation_stats = subject_summary.get("mutation_score_percent", {})
        recall_stats = subject_summary.get("reference_fault_recall_percent", {})
        line_stats = subject_summary.get("line_coverage_percent", {})
        branch_stats = subject_summary.get("branch_coverage_percent", {})
        reference_coverage = item.get("reference_coverage") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(subject.get("subject_id", "")),
                    str(subject.get("role", "")),
                    _fmt(reference.get("mutation_score")),
                    _fmt(mutation_stats.get("mean")),
                    _fmt(recall_stats.get("mean")),
                    f"{_fmt(reference_coverage.get('line_coverage_percent'))}/{_fmt(reference_coverage.get('branch_coverage_percent'))}",
                    f"{_fmt(line_stats.get('mean'))}/{_fmt(branch_stats.get('mean'))}",
                    str(subject_summary.get("valid_runs", 0)),
                    ", ".join(subject_summary.get("generation_backends", {}).keys()),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Metric definitions",
            "",
            "- Mutation score = killed valid mutants / (killed + survived valid mutants). Invalid and timed-out mutants are excluded and listed in raw artifacts.",
            "- Reference-fault recall = unique mutants killed by the generated suite / unique mutants killed by the manual reference suite.",
            "- Line and branch coverage use raw covered/total counts from coverage.py in an isolated project copy.",
            "- A generated suite is valid only when static validation passes, the clean source passes, and repeated outcomes are consistent.",
            "- End-to-end pipeline time and isolated pytest execution time are reported separately.",
            "",
            "## Statistical analysis",
            "",
            "Descriptive statistics include sample size, mean, median, standard deviation, range, and a deterministic bootstrap 95% confidence interval. Any paired comparison uses one aggregated observation per study subject; repeated runs are not misrepresented as independent projects.",
            "",
            "## Provenance and limitations",
            "",
            f"- Git commit: `{payload['provenance'].get('git_commit')}`",
            f"- Git worktree dirty: `{payload['provenance'].get('git_dirty')}`",
            f"- Python: `{payload['provenance'].get('python_version')}`",
            f"- Platform: `{payload['provenance'].get('platform')}`",
            "- Mutation operators can produce equivalent mutants; thesis-scale runs require manual review or an explicit equivalent-mutant protocol.",
            "- The bundled calculator subject is a harness smoke test only. It must not be presented as real-world validation.",
            "- This report intentionally provides no arbitrary weighted winner score.",
            "",
            f"Raw artifacts: `{payload['experiment_directory']}/raw`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_provenance(config: ExperimentConfig) -> dict[str, Any]:
    commit = _git_output(config.repo_root, ["rev-parse", "HEAD"])
    status = _git_output(config.repo_root, ["status", "--porcelain"])
    dependencies: dict[str, str | None] = {}
    for package in ("pytest", "coverage", "google-genai", "pandas", "matplotlib"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit or None,
        "git_dirty": bool(status),
        "git_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "dependencies": dependencies,
        "model": config.model,
        "temperature": config.temperature,
        "base_seed": config.base_seed,
        "offline_requested": config.offline,
    }


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=os.environ.copy(),
        )
        return_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as error:
        return_code = None
        stdout = _timeout_text(error.stdout)
        stderr = _timeout_text(error.stderr)
        timed_out = True
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "command": command,
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "stdout": stdout,
        "stderr": stderr,
    }


def _config_payload(config: ExperimentConfig) -> dict[str, Any]:
    return {
        "subjects": [asdict(subject) for subject in config.subjects],
        "runs": config.runs,
        "model": config.model,
        "temperature": config.temperature,
        "base_seed": config.base_seed,
        "offline": config.offline,
        "stability_runs": config.stability_runs,
        "mutation_limit": config.mutation_limit,
        "mutation_timeout_seconds": config.mutation_timeout_seconds,
        "pipeline_timeout_seconds": config.pipeline_timeout_seconds,
        "max_heal_attempts": config.max_heal_attempts,
        "minimum_study_subjects": config.minimum_study_subjects,
        "minimum_study_projects": config.minimum_study_projects,
        "minimum_killable_faults": config.minimum_killable_faults,
        "minimum_generation_runs": config.minimum_generation_runs,
        "minimum_stability_runs": config.minimum_stability_runs,
        "equivalent_mutant_protocol": (
            _relative_or_absolute(config.equivalent_mutant_protocol, config.repo_root)
            if config.equivalent_mutant_protocol
            else None
        ),
        "allow_uncontained_llm_tests": config.allow_uncontained_llm_tests,
    }


def _resolve_output_directory(root: Path, output_root: Path, run_id: str) -> Path:
    base = output_root if output_root.is_absolute() else root / output_root
    resolved_base = base.resolve()
    try:
        resolved_base.relative_to(root)
    except ValueError as error:
        raise ValueError("Experiment output_root must remain inside the repository") from error
    return resolved_base / _safe_name(run_id)


def _require_relative_existing(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} path must stay inside repository: {value}") from error
    if not path.exists():
        raise FileNotFoundError(f"{label} path not found: {value}")
    return path


def _safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    if not cleaned:
        raise ValueError("Identifier must contain at least one safe character")
    return cleaned


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
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


__all__ = [
    "ExperimentConfig",
    "ExperimentRunner",
    "SubjectConfig",
    "load_subject_manifest",
]
