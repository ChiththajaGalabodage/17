from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


DEFAULT_OUTPUT = Path("reports/execution_time_comparison.png")
DEFAULT_INPUT = Path("reports/comparison_report.csv")
STRATEGY_ORDER = ["agentic", "traditional"]
STRATEGY_COLORS = {
    "agentic": "#1f4e79",
    "traditional": "#c46a00",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a clustered bar chart for benchmark execution time from comparison CSV reports."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=[str(DEFAULT_INPUT)],
        help="One or more benchmark CSV report paths.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output image path for the chart (PNG by default).",
    )
    parser.add_argument(
        "--title",
        default="Benchmark Execution Time by Strategy",
        help="Chart title.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI used when saving the figure.",
    )
    return parser.parse_args()


def load_benchmark_csvs(paths: list[str | Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for raw_path in paths:
        path = Path(raw_path)
        frame = pd.read_csv(path)
        frame["source_report"] = path.stem
        frames.append(frame)

    if not frames:
        raise ValueError("No CSV inputs were provided.")

    combined = pd.concat(frames, ignore_index=True)
    required_columns = {"strategy", "run", "duration_seconds"}
    missing = required_columns.difference(combined.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    combined = combined.copy()
    combined["strategy"] = combined["strategy"].astype(str).str.strip().str.lower()
    combined["run"] = pd.to_numeric(combined["run"], errors="coerce").astype("Int64")
    combined["duration_seconds"] = pd.to_numeric(combined["duration_seconds"], errors="coerce")
    combined = combined.dropna(subset=["strategy", "run", "duration_seconds"])
    combined = combined[combined["strategy"].isin(STRATEGY_ORDER)]
    return combined


def summarize_execution_time(frame: pd.DataFrame) -> pd.DataFrame:
    summary = (
        frame.groupby(["run", "strategy"], as_index=False)
        .agg(
            mean_duration=("duration_seconds", "mean"),
            std_duration=("duration_seconds", "std"),
            count=("duration_seconds", "count"),
        )
    )
    summary["std_duration"] = summary["std_duration"].fillna(0.0)
    summary["run"] = summary["run"].astype(int)
    summary["strategy"] = pd.Categorical(summary["strategy"], categories=STRATEGY_ORDER, ordered=True)
    return summary.sort_values(["run", "strategy"]).reset_index(drop=True)


def plot_execution_time(summary: pd.DataFrame, output: str | Path, title: str, dpi: int = 300) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    run_values = sorted(summary["run"].unique())
    strategy_values = [strategy for strategy in STRATEGY_ORDER if strategy in set(summary["strategy"].astype(str))]

    if not run_values:
        raise ValueError("No valid run values found to plot.")

    width = 0.36 if len(strategy_values) == 2 else 0.72 / max(len(strategy_values), 1)
    x_positions = range(len(run_values))

    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 16,
            "axes.labelsize": 12,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "figure.dpi": dpi,
            "savefig.dpi": dpi,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )

    fig, ax = plt.subplots(figsize=(10.5, 6.2), constrained_layout=True)

    for index, strategy in enumerate(strategy_values):
        strategy_frame = summary[summary["strategy"].astype(str) == strategy].set_index("run")
        offsets = [position + (index - (len(strategy_values) - 1) / 2) * width for position in x_positions]
        heights = [float(strategy_frame.loc[run, "mean_duration"]) if run in strategy_frame.index else 0.0 for run in run_values]
        errors = [float(strategy_frame.loc[run, "std_duration"]) if run in strategy_frame.index else 0.0 for run in run_values]

        bars = ax.bar(
            offsets,
            heights,
            width=width,
            label=strategy.capitalize(),
            color=STRATEGY_COLORS.get(strategy, "#4c4c4c"),
            edgecolor="#1f1f1f",
            linewidth=0.8,
            yerr=errors if any(errors) else None,
            capsize=4,
            alpha=0.95,
        )

        for bar, value in zip(bars, heights):
            ax.annotate(
                f"{value:.2f}",
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10,
                color="#222222",
            )

    ax.set_title(title)
    ax.set_xlabel("Run")
    ax.set_ylabel("Execution time (seconds)")
    ax.set_xticks(list(x_positions))
    ax.set_xticklabels([str(run) for run in run_values])
    ax.legend(frameon=False, ncol=min(2, len(strategy_values)), loc="upper right")
    ax.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.6)
    ax.set_axisbelow(True)

    max_height = float(summary["mean_duration"].max()) if not summary.empty else 0.0
    ax.set_ylim(0, max_height * 1.25 if max_height > 0 else 1.0)

    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> int:
    args = parse_args()
    frame = load_benchmark_csvs(args.inputs)
    summary = summarize_execution_time(frame)
    output_path = plot_execution_time(summary, args.output, args.title, dpi=args.dpi)
    print(f"Wrote chart to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())