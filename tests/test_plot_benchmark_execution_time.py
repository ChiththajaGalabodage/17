from pathlib import Path

import pandas as pd

from scripts.plot_benchmark_execution_time import (
    load_benchmark_csvs,
    plot_execution_time,
    summarize_execution_time,
)


def test_summarize_execution_time_groups_by_strategy_and_run(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "comparison_report.csv"
    pd.DataFrame(
        {
            "run": [1, 1, 2, 2, 3, 3],
            "strategy": [
                "agentic",
                "traditional",
                "agentic",
                "traditional",
                "agentic",
                "traditional",
            ],
            "duration_seconds": [2.575, 0.698, 1.85, 0.634, 1.7, 0.679],
        }
    ).to_csv(benchmark_path, index=False)

    frame = load_benchmark_csvs([benchmark_path])
    summary = summarize_execution_time(frame)

    assert list(summary["run"]) == [1, 1, 2, 2, 3, 3]
    assert list(summary["strategy"].astype(str)) == ["agentic", "traditional", "agentic", "traditional", "agentic", "traditional"]
    assert summary.loc[summary["strategy"].astype(str) == "agentic", "mean_duration"].tolist() == [2.575, 1.85, 1.7]
    assert summary.loc[summary["strategy"].astype(str) == "traditional", "mean_duration"].tolist() == [0.698, 0.634, 0.679]


def test_plot_execution_time_writes_file(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "run": [1, 1, 2, 2],
            "strategy": ["agentic", "traditional", "agentic", "traditional"],
            "mean_duration": [2.0, 1.0, 1.5, 0.8],
            "std_duration": [0.1, 0.05, 0.0, 0.0],
            "count": [1, 1, 1, 1],
        }
    )

    output_path = plot_execution_time(frame, tmp_path / "chart.png", "Demo Chart", dpi=100)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
