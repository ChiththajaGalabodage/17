"""CLI for controlled, labelled self-healing experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.healing_experiment import (  # noqa: E402
    HealingExperimentConfig,
    HealingExperimentRunner,
    load_healing_manifest,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate constrained test healing against labelled scenarios"
    )
    parser.add_argument(
        "--manifest",
        default="experiments/healing_scenarios.example.json",
        help="Versioned healing scenario manifest",
    )
    parser.add_argument("--output-root", default="reports/healing_runs")
    parser.add_argument("--run-id")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=4885)
    parser.add_argument("--test-timeout", type=float, default=30.0)
    parser.add_argument("--mutation-timeout", type=float, default=30.0)
    parser.add_argument(
        "--mutation-limit",
        type=int,
        help="Override each scenario's deterministic mutant limit",
    )
    parser.add_argument("--minimum-study-scenarios", type=int, default=30)
    parser.add_argument("--minimum-study-projects", type=int, default=3)
    parser.add_argument(
        "--claim-llm-effect",
        action="store_true",
        help="Request evaluation of the live-Gemini effect claim",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    output_root = Path(args.output_root)
    try:
        scenarios = load_healing_manifest(manifest)
        config = HealingExperimentConfig(
            repo_root=ROOT,
            scenarios=scenarios,
            output_root=output_root,
            run_id=args.run_id,
            offline=args.offline,
            model=args.model,
            temperature=args.temperature,
            seed=args.seed,
            test_timeout_seconds=args.test_timeout,
            mutation_timeout_seconds=args.mutation_timeout,
            mutation_limit_override=args.mutation_limit,
            minimum_study_scenarios=args.minimum_study_scenarios,
            minimum_study_projects=args.minimum_study_projects,
            claim_llm_effect=args.claim_llm_effect,
        )
        payload = HealingExperimentRunner(config).run()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Self-healing experiment configuration error: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "experiment_directory": payload["experiment_directory"],
                "evidence_readiness": payload["summary"]["evidence_readiness"],
                "claim_support": payload["summary"]["claim_support"],
                "invalid_scenarios": payload["summary"]["invalid_scenario_count"],
            },
            indent=2,
        )
    )
    return 2 if payload["summary"]["invalid_scenario_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
