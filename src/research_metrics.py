"""Metric definitions used by the research experiment.

The functions in this module operate on explicit ground-truth sets and paired
observations. They deliberately avoid proxy definitions such as treating every
failed test as a separate defect or treating selection percentage as accuracy.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from statistics import mean, median, stdev
from typing import Any


def selection_metrics(
    selected: Iterable[str],
    relevant: Iterable[str],
    universe: Iterable[str],
) -> dict[str, Any]:
    """Return precision/recall/F1 and reduction against an oracle test set."""
    universe_set = set(universe)
    selected_set = set(selected)
    relevant_set = set(relevant)
    unknown_selected = selected_set - universe_set
    unknown_relevant = relevant_set - universe_set
    if unknown_selected:
        raise ValueError(f"Selected tests outside universe: {sorted(unknown_selected)}")
    if unknown_relevant:
        raise ValueError(f"Relevant tests outside universe: {sorted(unknown_relevant)}")

    tp = len(selected_set & relevant_set)
    fp = len(selected_set - relevant_set)
    fn = len(relevant_set - selected_set)
    tn = len(universe_set - selected_set - relevant_set)
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    selected_fraction = _safe_ratio(len(selected_set), len(universe_set))
    return {
        "universe_count": len(universe_set),
        "relevant_count": len(relevant_set),
        "selected_count": len(selected_set),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "selected_fraction": round(selected_fraction, 4),
        "test_reduction": round(1.0 - selected_fraction, 4),
    }


def fault_detection_metrics(
    killed_fault_ids: Iterable[str],
    killable_fault_ids: Iterable[str],
) -> dict[str, Any]:
    """Measure unique-fault recall from explicit killable fault IDs."""
    killed = set(killed_fault_ids)
    killable = set(killable_fault_ids)
    unknown = killed - killable
    if unknown:
        raise ValueError(f"Killed fault IDs outside ground truth: {sorted(unknown)}")
    missed = killable - killed
    recall = _safe_ratio(len(killed), len(killable))
    return {
        "killable_fault_count": len(killable),
        "killed_fault_count": len(killed),
        "missed_fault_count": len(missed),
        "killed_fault_ids": sorted(killed),
        "missed_fault_ids": sorted(missed),
        "fault_recall": round(recall, 4),
        "missed_fault_rate": round(1.0 - recall if killable else 0.0, 4),
    }


def descriptive_statistics(
    values: Sequence[float],
    *,
    bootstrap_samples: int = 2000,
    seed: int = 4885,
) -> dict[str, Any]:
    """Return transparent descriptive statistics and a bootstrap mean CI."""
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "minimum": None,
            "maximum": None,
            "bootstrap_mean_ci95": [None, None],
        }
    ci = bootstrap_mean_ci(
        clean,
        samples=max(int(bootstrap_samples), 1),
        seed=seed,
    )
    return {
        "n": len(clean),
        "mean": round(mean(clean), 6),
        "median": round(median(clean), 6),
        "standard_deviation": round(stdev(clean), 6) if len(clean) > 1 else 0.0,
        "minimum": round(min(clean), 6),
        "maximum": round(max(clean), 6),
        "bootstrap_mean_ci95": [round(ci[0], 6), round(ci[1], 6)],
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int = 2000,
    seed: int = 4885,
) -> tuple[float, float]:
    if not values:
        raise ValueError("At least one observation is required")
    if samples <= 0:
        raise ValueError("samples must be greater than zero")
    data = [float(value) for value in values]
    rng = random.Random(seed)
    resampled_means = sorted(
        mean(rng.choices(data, k=len(data))) for _ in range(samples)
    )
    lower_index = max(0, math.floor(0.025 * (samples - 1)))
    upper_index = min(samples - 1, math.ceil(0.975 * (samples - 1)))
    return resampled_means[lower_index], resampled_means[upper_index]


def paired_comparison(
    proposed: Sequence[float],
    baseline: Sequence[float],
    *,
    higher_is_better: bool = True,
    bootstrap_samples: int = 2000,
    seed: int = 4885,
) -> dict[str, Any]:
    """Analyze paired observations without inventing an overall winner score."""
    if len(proposed) != len(baseline):
        raise ValueError("Paired strategies must contain the same number of observations")
    if not proposed:
        raise ValueError("At least one paired observation is required")
    raw_differences = [float(a) - float(b) for a, b in zip(proposed, baseline, strict=True)]
    oriented = raw_differences if higher_is_better else [-value for value in raw_differences]
    ci = bootstrap_mean_ci(
        raw_differences,
        samples=max(int(bootstrap_samples), 1),
        seed=seed,
    )
    difference_sd = stdev(raw_differences) if len(raw_differences) > 1 else 0.0
    effect_size = mean(raw_differences) / difference_sd if difference_sd else None
    wins = sum(value > 0 for value in oriented)
    losses = sum(value < 0 for value in oriented)
    ties = sum(value == 0 for value in oriented)
    return {
        "n_pairs": len(raw_differences),
        "mean_difference_proposed_minus_baseline": round(mean(raw_differences), 6),
        "median_difference_proposed_minus_baseline": round(median(raw_differences), 6),
        "bootstrap_mean_difference_ci95": [round(ci[0], 6), round(ci[1], 6)],
        "paired_effect_size_dz": round(effect_size, 6) if effect_size is not None else None,
        "wins_in_expected_direction": wins,
        "losses_in_expected_direction": losses,
        "ties": ties,
        "two_sided_sign_test_p": round(_two_sided_sign_test(wins, losses), 6),
        "higher_is_better": higher_is_better,
        "raw_differences": [round(value, 6) for value in raw_differences],
    }


def _two_sided_sign_test(wins: int, losses: int) -> float:
    non_ties = wins + losses
    if non_ties == 0:
        return 1.0
    tail = min(wins, losses)
    probability = sum(
        math.comb(non_ties, index) * (0.5**non_ties)
        for index in range(tail + 1)
    )
    return min(1.0, 2.0 * probability)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


__all__ = [
    "bootstrap_mean_ci",
    "descriptive_statistics",
    "fault_detection_metrics",
    "paired_comparison",
    "selection_metrics",
]
