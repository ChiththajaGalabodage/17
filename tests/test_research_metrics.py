import pytest

from src.research_metrics import (
    descriptive_statistics,
    fault_detection_metrics,
    paired_comparison,
    selection_metrics,
)


def test_selection_metrics_use_oracle_relevance_not_selection_percentage():
    result = selection_metrics(
        selected={"test_a", "test_b"},
        relevant={"test_a", "test_c"},
        universe={"test_a", "test_b", "test_c", "test_d"},
    )

    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["f1"] == 0.5
    assert result["test_reduction"] == 0.5


def test_fault_detection_counts_unique_ground_truth_ids():
    result = fault_detection_metrics(
        killed_fault_ids=["F-1", "F-1"],
        killable_fault_ids=["F-1", "F-2"],
    )

    assert result["killed_fault_count"] == 1
    assert result["fault_recall"] == 0.5
    assert result["missed_fault_ids"] == ["F-2"]


def test_metrics_reject_values_outside_ground_truth():
    with pytest.raises(ValueError, match="outside universe"):
        selection_metrics(["unknown"], [], ["known"])
    with pytest.raises(ValueError, match="outside ground truth"):
        fault_detection_metrics(["unknown"], ["F-1"])


def test_descriptive_statistics_and_pairing_are_reproducible():
    summary = descriptive_statistics([1.0, 2.0, 3.0], bootstrap_samples=100)
    paired = paired_comparison(
        proposed=[3.0, 4.0, 5.0],
        baseline=[2.0, 4.0, 4.0],
        bootstrap_samples=100,
    )

    assert summary["n"] == 3
    assert summary["mean"] == 2.0
    assert paired["n_pairs"] == 3
    assert paired["wins_in_expected_direction"] == 2
    assert paired["ties"] == 1
