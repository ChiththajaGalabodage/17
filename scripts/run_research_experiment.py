"""Command-line entrypoint for the research experiment pipeline."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_experiment import ExperimentConfig, ExperimentRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run agentic vs traditional testing experiments.")
    parser.add_argument("--source", default="target_code.py", help="Target Python source module")
    parser.add_argument("--runs", type=int, default=3, help="Runs per strategy")
    parser.add_argument("--base-ref", default="HEAD~1", help="Git base ref for predictive selection")
    parser.add_argument("--max-heal-attempts", type=int, default=2, help="Maximum self-heal retries")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--output-dir", default="reports", help="Output directory for reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    repo_root = ROOT
    config = ExperimentConfig(
        repo_root=repo_root,
        source=args.source,
        runs=args.runs,
        base_ref=args.base_ref,
        max_heal_attempts=args.max_heal_attempts,
        model=args.model,
        output_dir=Path(args.output_dir),
        charts_dir=Path(args.output_dir) / "charts",
        results_csv=Path(args.output_dir) / "results.csv",
        comparison_csv=Path(args.output_dir) / "comparison_matrix.csv",
        summary_json=Path(args.output_dir) / "summary.json",
        research_report_md=Path(args.output_dir) / "research_report.md",
    )
    runner = ExperimentRunner(config)
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
