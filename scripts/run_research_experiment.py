"""CLI for the evidence-preserving research experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_experiment import (
    ExperimentConfig,
    ExperimentRunner,
    SubjectConfig,
    load_subject_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare generated and manual suites using clean baselines, "
            "unique mutation IDs, repeated-run consistency, and raw provenance."
        )
    )
    parser.add_argument(
        "--manifest",
        default="experiments/subjects.example.json",
        help="Versioned JSON subject manifest (schema version 1)",
    )
    parser.add_argument("--source", help="Single-subject source (overrides --manifest)")
    parser.add_argument(
        "--manual-test",
        action="append",
        default=[],
        help="Single-subject manual pytest target; repeat for multiple targets",
    )
    parser.add_argument("--subject-id", default="custom-subject")
    parser.add_argument(
        "--subject-role",
        choices=("study", "demo"),
        default="study",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--stability-runs", type=int, default=3)
    parser.add_argument("--mutation-limit", type=int, default=20)
    parser.add_argument("--mutation-timeout", type=float, default=30.0)
    parser.add_argument("--pipeline-timeout", type=float, default=300.0)
    parser.add_argument("--max-heal-attempts", type=int, default=2)
    parser.add_argument("--minimum-target-coverage", type=float, default=50.0)
    parser.add_argument(
        "--equivalent-mutant-protocol",
        help="Versioned protocol/review document required for study evidence readiness",
    )
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--base-seed", type=int, default=4885)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use deterministic fallback; such runs are explicitly ineligible for an LLM-effect claim",
    )
    parser.add_argument(
        "--allow-uncontained-llm-tests",
        action="store_true",
        help="Acknowledge that the whole experiment is already inside a disposable restricted container/VM",
    )
    parser.add_argument("--output-root", default="reports/research_runs")
    parser.add_argument("--run-id", help="Optional unique output directory name")
    return parser.parse_args()


def _subjects(args: argparse.Namespace) -> tuple[SubjectConfig, ...]:
    if args.source:
        if not args.manual_test:
            raise ValueError("--source requires at least one --manual-test")
        return (
            SubjectConfig(
                subject_id=args.subject_id,
                source=args.source,
                manual_tests=tuple(args.manual_test),
                role=args.subject_role,
                minimum_target_coverage=args.minimum_target_coverage,
                project_id=args.subject_id,
            ),
        )
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    return load_subject_manifest(manifest)


def main() -> int:
    args = parse_args()
    try:
        subjects = _subjects(args)
        config = ExperimentConfig(
            repo_root=ROOT,
            subjects=subjects,
            runs=args.runs,
            model=args.model,
            temperature=args.temperature,
            base_seed=args.base_seed,
            offline=args.offline,
            stability_runs=args.stability_runs,
            mutation_limit=args.mutation_limit,
            mutation_timeout_seconds=args.mutation_timeout,
            pipeline_timeout_seconds=args.pipeline_timeout,
            max_heal_attempts=args.max_heal_attempts,
            output_root=Path(args.output_root),
            run_id=args.run_id,
            equivalent_mutant_protocol=(
                Path(args.equivalent_mutant_protocol)
                if args.equivalent_mutant_protocol
                else None
            ),
            allow_uncontained_llm_tests=args.allow_uncontained_llm_tests,
        )
        payload = ExperimentRunner(config).run()
    except (FileNotFoundError, ValueError) as error:
        print(f"Experiment configuration error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(
        {
            "experiment_directory": payload["experiment_directory"],
            "evidence_readiness": payload["summary"]["evidence_readiness"],
            "claim_support": payload["summary"]["claim_support"],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
