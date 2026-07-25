"""Run oracle-based change-impact versus LLM-hybrid selection evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.selection_experiment import (
    SelectionExperimentConfig,
    SelectionExperimentRunner,
    load_selection_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="experiments/selection_scenarios.example.json",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--base-seed", type=int, default=4885)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output-root", default="reports/selection_runs")
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    try:
        config = SelectionExperimentConfig(
            repo_root=ROOT,
            scenarios=load_selection_manifest(manifest),
            runs=args.runs,
            model=args.model,
            temperature=args.temperature,
            base_seed=args.base_seed,
            offline=args.offline,
            output_root=Path(args.output_root),
            run_id=args.run_id,
        )
        payload = SelectionExperimentRunner(config).run()
    except (FileNotFoundError, ValueError) as error:
        print(f"Selection experiment configuration error: {error}", file=sys.stderr)
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
