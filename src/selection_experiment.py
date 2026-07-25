"""Oracle-based evaluation for LLM-assisted test selection."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from src.research_metrics import descriptive_statistics, paired_comparison, selection_metrics
from src.test_select_agent import TestSelectAgent


@dataclass(frozen=True, slots=True)
class SelectionScenario:
    scenario_id: str
    changed_files: tuple[str, ...]
    relevant_tests: tuple[str, ...]
    changed_symbols: tuple[str, ...] = ()
    tests_dir: str = "tests"
    role: str = "study"
    oracle_source: str = ""
    project_id: str | None = None
    base_revision: str | None = None
    head_revision: str | None = None
    diff_path: str | None = None
    diff_sha256: str | None = None
    oracle_artifact: str | None = None
    oracle_sha256: str | None = None
    oracle_method: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SelectionScenario":
        changed = payload.get("changed_files")
        relevant = payload.get("relevant_tests")
        if not isinstance(changed, list) or not changed:
            raise ValueError("Selection scenario requires changed_files")
        if not isinstance(relevant, list) or not relevant:
            raise ValueError("Selection scenario requires relevant_tests")
        oracle_source = str(payload.get("oracle_source", "")).strip()
        if not oracle_source:
            raise ValueError("Selection scenario requires a documented oracle_source")
        return cls(
            scenario_id=str(payload["id"]),
            changed_files=tuple(str(item) for item in changed),
            relevant_tests=tuple(str(item) for item in relevant),
            changed_symbols=tuple(str(item) for item in payload.get("changed_symbols", [])),
            tests_dir=str(payload.get("tests_dir", "tests")),
            role=str(payload.get("role", "study")),
            oracle_source=oracle_source,
            project_id=(str(payload["project_id"]) if payload.get("project_id") else None),
            base_revision=(
                str(payload["base_revision"]) if payload.get("base_revision") else None
            ),
            head_revision=(
                str(payload["head_revision"]) if payload.get("head_revision") else None
            ),
            diff_path=(str(payload["diff_path"]) if payload.get("diff_path") else None),
            diff_sha256=(
                str(payload["diff_sha256"]).lower()
                if payload.get("diff_sha256")
                else None
            ),
            oracle_artifact=(
                str(payload["oracle_artifact"])
                if payload.get("oracle_artifact")
                else None
            ),
            oracle_sha256=(
                str(payload["oracle_sha256"]).lower()
                if payload.get("oracle_sha256")
                else None
            ),
            oracle_method=(
                str(payload["oracle_method"]) if payload.get("oracle_method") else None
            ),
        )


@dataclass(slots=True)
class SelectionExperimentConfig:
    repo_root: Path
    scenarios: tuple[SelectionScenario, ...]
    runs: int = 3
    model: str = "gemini-2.5-flash"
    temperature: float = 0.0
    base_seed: int = 4885
    offline: bool = False
    output_root: Path = Path("reports/selection_runs")
    run_id: str | None = None
    minimum_study_scenarios: int = 30
    minimum_study_projects: int = 3
    minimum_runs: int = 3

    def validate(self) -> None:
        self.repo_root = self.repo_root.resolve()
        if not self.repo_root.is_dir():
            raise FileNotFoundError(f"Repository root not found: {self.repo_root}")
        if self.runs <= 0:
            raise ValueError("runs must be positive")
        if not self.scenarios:
            raise ValueError("At least one selection scenario is required")
        ids = [item.scenario_id for item in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Selection scenario IDs must be unique")
        for scenario in self.scenarios:
            if scenario.role not in {"study", "demo"}:
                raise ValueError("Selection scenario role must be 'study' or 'demo'")
            tests_dir = (self.repo_root / scenario.tests_dir).resolve()
            _inside_root(tests_dir, self.repo_root)
            if not tests_dir.is_dir():
                raise FileNotFoundError(f"tests_dir not found: {scenario.tests_dir}")
            for changed_file in scenario.changed_files:
                changed_path = (self.repo_root / changed_file).resolve()
                _inside_root(changed_path, self.repo_root)
                if not changed_path.exists():
                    raise FileNotFoundError(f"changed file not found: {changed_file}")
            for relevant_test in scenario.relevant_tests:
                relevant_path = (self.repo_root / relevant_test.partition("::")[0]).resolve()
                _inside_root(relevant_path, self.repo_root)
                if not relevant_path.is_file():
                    raise FileNotFoundError(f"relevant test not found: {relevant_test}")
            for artifact in (scenario.diff_path, scenario.oracle_artifact):
                if artifact:
                    artifact_path = (self.repo_root / artifact).resolve()
                    _inside_root(artifact_path, self.repo_root)
                    if not artifact_path.is_file():
                        raise FileNotFoundError(f"selection evidence artifact not found: {artifact}")


class SelectionExperimentRunner:
    def __init__(self, config: SelectionExperimentConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Any]:
        self.config.validate()
        run_id = self.config.run_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        output_dir = _output_dir(self.config, run_id)
        output_dir.mkdir(parents=True, exist_ok=False)

        results = [self._evaluate_scenario(scenario) for scenario in self.config.scenarios]
        summary = _summarize(self.config, results)
        payload = {
            "schema_version": 2,
            "experiment_id": run_id,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": {
                "runs": self.config.runs,
                "model": self.config.model,
                "temperature": self.config.temperature,
                "base_seed": self.config.base_seed,
                "offline": self.config.offline,
                "minimum_study_scenarios": self.config.minimum_study_scenarios,
                "minimum_study_projects": self.config.minimum_study_projects,
                "minimum_runs": self.config.minimum_runs,
            },
            "provenance": _provenance(self.config),
            "scenarios": results,
            "summary": summary,
        }
        _write_json(output_dir / "summary.json", payload)
        _write_csv(output_dir / "results.csv", results)
        _write_report(output_dir / "selection_report.md", payload)
        payload["experiment_directory"] = output_dir.relative_to(
            self.config.repo_root
        ).as_posix()
        return payload

    def _evaluate_scenario(self, scenario: SelectionScenario) -> dict[str, Any]:
        diff_observed_sha256 = _optional_file_sha256(
            self.config.repo_root, scenario.diff_path
        )
        oracle_observed_sha256 = _optional_file_sha256(
            self.config.repo_root, scenario.oracle_artifact
        )
        change_identity = hashlib.sha256(
            json.dumps(
                {
                    "project_id": scenario.project_id,
                    "base_revision": scenario.base_revision,
                    "head_revision": scenario.head_revision,
                    "diff_sha256": diff_observed_sha256 or scenario.diff_sha256,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        baseline_selector = TestSelectAgent(
            str(self.config.repo_root),
            api_key="",
            model=self.config.model,
            use_llm=False,
            temperature=self.config.temperature,
            seed=self.config.base_seed,
        )
        baseline = baseline_selector.select_with_evidence(
            list(scenario.changed_files),
            tests_dir=scenario.tests_dir,
            use_llm=False,
            changed_symbols=list(scenario.changed_symbols),
            change_context=baseline_selector.build_change_context(
                list(scenario.changed_files), list(scenario.changed_symbols)
            ),
        )
        baseline_metrics = selection_metrics(
            baseline["selected"],
            scenario.relevant_tests,
            baseline["universe"],
        )

        proposed_runs: list[dict[str, Any]] = []
        for run_index in range(1, self.config.runs + 1):
            selector = TestSelectAgent(
                str(self.config.repo_root),
                api_key="" if self.config.offline else None,
                model=self.config.model,
                use_llm=not self.config.offline,
                temperature=self.config.temperature,
                seed=self.config.base_seed + run_index - 1,
            )
            evidence = selector.select_with_evidence(
                list(scenario.changed_files),
                tests_dir=scenario.tests_dir,
                use_llm=not self.config.offline,
                changed_symbols=list(scenario.changed_symbols),
                change_context=selector.build_change_context(
                    list(scenario.changed_files), list(scenario.changed_symbols)
                ),
            )
            metrics = selection_metrics(
                evidence["selected"],
                scenario.relevant_tests,
                evidence["universe"],
            )
            proposed_runs.append(
                {"run": run_index, "evidence": evidence, "metrics": metrics}
            )

        return {
            "scenario": asdict(scenario),
            "integrity": {
                "change_identity_sha256": change_identity,
                "observed_diff_sha256": diff_observed_sha256,
                "diff_hash_matches": bool(
                    scenario.diff_sha256
                    and diff_observed_sha256
                    and scenario.diff_sha256 == diff_observed_sha256
                ),
                "observed_oracle_sha256": oracle_observed_sha256,
                "oracle_hash_matches": bool(
                    scenario.oracle_sha256
                    and oracle_observed_sha256
                    and scenario.oracle_sha256 == oracle_observed_sha256
                ),
            },
            "baseline": {"evidence": baseline, "metrics": baseline_metrics},
            "proposed_runs": proposed_runs,
        }


def load_selection_manifest(path: str | Path) -> tuple[SelectionScenario, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Selection manifest schema_version must be 1")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Selection manifest requires a non-empty scenarios list")
    return tuple(SelectionScenario.from_dict(item) for item in scenarios)


def _summarize(
    config: SelectionExperimentConfig,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    study = [
        item for item in results if item["scenario"].get("role", "study") == "study"
    ]
    all_proposed = [run for item in study for run in item["proposed_runs"]]
    backend_counts = Counter(
        run["evidence"].get("backend", "unknown") for run in all_proposed
    )
    fallback_runs = sum(bool(run["evidence"].get("fallback")) for run in all_proposed)
    non_llm_runs = sum(
        run["evidence"].get("backend") != "gemini-hybrid"
        for run in all_proposed
    )
    reasons: list[str] = []
    if len(study) < config.minimum_study_scenarios:
        reasons.append(
            f"Only {len(study)} study scenario(s); at least {config.minimum_study_scenarios} are required by this protocol."
        )
    project_ids = {
        item["scenario"].get("project_id")
        for item in study
        if item["scenario"].get("project_id")
    }
    if len(project_ids) < config.minimum_study_projects:
        reasons.append(
            f"Only {len(project_ids)} unique pinned study project(s); at least "
            f"{config.minimum_study_projects} are required by this protocol."
        )
    if config.runs < config.minimum_runs:
        reasons.append(
            f"Only {config.runs} proposed run(s) per scenario; at least "
            f"{config.minimum_runs} are required by this protocol."
        )
    missing_immutable_evidence = [
        item["scenario"]["scenario_id"]
        for item in study
        if not item["scenario"].get("project_id")
        or not item["scenario"].get("base_revision")
        or not item["scenario"].get("head_revision")
        or not item["scenario"].get("diff_path")
        or not item["scenario"].get("diff_sha256")
        or not item["scenario"].get("oracle_artifact")
        or not item["scenario"].get("oracle_sha256")
        or item["scenario"].get("oracle_method") != "full-suite-execution"
    ]
    if missing_immutable_evidence:
        reasons.append(
            "Study scenarios missing pinned revisions, hashed diff/oracle artifacts, or "
            "oracle_method=full-suite-execution: "
            + ", ".join(sorted(missing_immutable_evidence))
            + "."
        )
    integrity_failures = [
        item["scenario"]["scenario_id"]
        for item in study
        if not item.get("integrity", {}).get("diff_hash_matches")
        or not item.get("integrity", {}).get("oracle_hash_matches")
    ]
    if integrity_failures:
        reasons.append(
            "Diff/oracle artifact hash verification failed for: "
            + ", ".join(sorted(integrity_failures))
            + "."
        )
    change_identities = [
        item.get("integrity", {}).get("change_identity_sha256") for item in study
    ]
    if len(change_identities) != len(set(change_identities)):
        reasons.append(
            "Duplicate immutable change identities were found; repeated scenarios cannot inflate evidence volume."
        )
    if fallback_runs:
        reasons.append(
            f"{fallback_runs} proposed study run(s) used deterministic fallback, so they cannot support an LLM-selection claim."
        )
    if non_llm_runs:
        reasons.append(
            f"{non_llm_runs} proposed study run(s) did not use the Gemini hybrid backend."
        )
    if not study:
        reasons.append("The manifest contains only demo scenarios.")
    if study and _git_status(config.repo_root):
        reasons.append(
            "The study worktree is dirty; selection scenarios must use immutable clean revisions."
        )

    metric_summaries: dict[str, Any] = {}
    paired: dict[str, Any] = {}
    for metric, higher in (("recall", True), ("precision", True), ("f1", True), ("test_reduction", True)):
        proposed_values = [
            float(run["metrics"][metric]) for run in all_proposed
        ]
        metric_summaries[metric] = descriptive_statistics(proposed_values)
        proposed_by_scenario: list[float] = []
        baseline_by_scenario: list[float] = []
        for item in study:
            values = [float(run["metrics"][metric]) for run in item["proposed_runs"]]
            if values:
                proposed_by_scenario.append(mean(values))
                baseline_by_scenario.append(float(item["baseline"]["metrics"][metric]))
        if proposed_by_scenario:
            paired[metric] = paired_comparison(
                proposed_by_scenario,
                baseline_by_scenario,
                higher_is_better=higher,
            )

    evidence_readiness = {
        "ready": not reasons,
        "scope": "llm-assisted-test-selection",
        "reasons": reasons,
        "note": "Evidence readiness does not imply that the LLM-assisted selector is superior.",
    }
    return {
        "study_scenarios": len(study),
        "unique_study_projects": len(project_ids),
        "unique_change_identities": len(set(change_identities)),
        "proposed_backend_counts": dict(sorted(backend_counts.items())),
        "fallback_study_runs": fallback_runs,
        "non_llm_study_runs": non_llm_runs,
        "proposed_metric_summaries": metric_summaries,
        "paired_proposed_vs_change_impact": paired,
        "evidence_readiness": evidence_readiness,
        "claim_readiness": {
            **evidence_readiness,
            "compatibility_note": (
                "Legacy field name; this is evidence readiness, not claim support."
            ),
        },
        "claim_support": {
            "assessed": False,
            "supports_superiority": None,
            "reasons": [
                "Apply preregistered recall, reduction, and uncertainty criteria to an evidence-ready study."
            ],
        },
    }


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "scenario_id",
        "role",
        "strategy",
        "run",
        "backend",
        "fallback",
        "selected_count",
        "universe_count",
        "precision",
        "recall",
        "f1",
        "test_reduction",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            scenario = item["scenario"]
            entries = [("change-impact", 1, item["baseline"])] + [
                ("llm-hybrid", run["run"], run) for run in item["proposed_runs"]
            ]
            for strategy, run_index, entry in entries:
                evidence = entry["evidence"]
                metrics = entry["metrics"]
                writer.writerow(
                    {
                        "scenario_id": scenario["scenario_id"],
                        "role": scenario["role"],
                        "strategy": strategy,
                        "run": run_index,
                        "backend": evidence["backend"],
                        "fallback": evidence["fallback"],
                        "selected_count": metrics["selected_count"],
                        "universe_count": metrics["universe_count"],
                        "precision": metrics["precision"],
                        "recall": metrics["recall"],
                        "f1": metrics["f1"],
                        "test_reduction": metrics["test_reduction"],
                    }
                )


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    readiness = payload["summary"]["evidence_readiness"]
    lines = [
        "# Test Selection Experiment",
        "",
        (
            "This run meets the configured selection-evidence checks."
            if readiness["ready"]
            else "This run does **not** yet meet the configured selection-evidence checks."
        ),
        "",
    ]
    lines.extend(f"- {reason}" for reason in readiness["reasons"])
    lines.extend(
        [
            "",
            "| Scenario | Role | Baseline recall | Baseline reduction | Proposed recall mean | Proposed reduction mean | Backend(s) |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload["scenarios"]:
        proposed_recall = mean(
            run["metrics"]["recall"] for run in item["proposed_runs"]
        )
        proposed_reduction = mean(
            run["metrics"]["test_reduction"] for run in item["proposed_runs"]
        )
        backends = sorted(
            {run["evidence"]["backend"] for run in item["proposed_runs"]}
        )
        lines.append(
            f"| {item['scenario']['scenario_id']} | {item['scenario']['role']} | "
            f"{item['baseline']['metrics']['recall']:.4f} | "
            f"{item['baseline']['metrics']['test_reduction']:.4f} | "
            f"{proposed_recall:.4f} | {proposed_reduction:.4f} | {', '.join(backends)} |"
        )
    lines.extend(
        [
            "",
            "Selection accuracy is not the selected-test percentage. Precision, recall, F1, and reduction are computed against versioned relevant-test oracles.",
            "",
            "The proposed selector is a safety-constrained LLM hybrid: deterministic impact matches cannot be dropped. It is not described as a learned predictive model unless historical training is added.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provenance(config: SelectionExperimentConfig) -> dict[str, Any]:
    status = _git_status(config.repo_root)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.repo_root,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        commit = ""
    manifest_fingerprint = hashlib.sha256(
        json.dumps([asdict(item) for item in config.scenarios], sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit or None,
        "git_dirty": bool(status),
        "git_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "scenario_manifest_sha256": manifest_fingerprint,
        "model": config.model,
        "temperature": config.temperature,
        "base_seed": config.base_seed,
        "offline_requested": config.offline,
    }


def _git_status(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
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


def _output_dir(config: SelectionExperimentConfig, run_id: str) -> Path:
    base = config.output_root if config.output_root.is_absolute() else config.repo_root / config.output_root
    base = base.resolve()
    _inside_root(base, config.repo_root)
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_id)
    if not safe:
        raise ValueError("run_id must contain a safe character")
    return base / safe


def _inside_root(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Path must remain inside repository: {path}") from error


def _optional_file_sha256(root: Path, relative: str | None) -> str | None:
    if not relative:
        return None
    path = (root / relative).resolve()
    _inside_root(path, root)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


__all__ = [
    "SelectionExperimentConfig",
    "SelectionExperimentRunner",
    "SelectionScenario",
    "load_selection_manifest",
]
