from __future__ import annotations

from src.reporter import build_report


def test_stage_duration_uses_earliest_running_and_latest_terminal_event() -> None:
    events = [
        {
            "stage": "analysis",
            "status": "running",
            "timestamp_utc": "2026-01-01T00:00:05+00:00",
        },
        {
            "stage": "analysis",
            "status": "completed",
            "timestamp_utc": "2026-01-01T00:00:07+00:00",
        },
        {
            "stage": "analysis",
            "status": "running",
            "timestamp_utc": "2026-01-01T00:00:01+00:00",
        },
        {
            "stage": "analysis",
            "status": "failed",
            "timestamp_utc": "2026-01-01T00:00:10+00:00",
        },
    ]

    report = build_report(
        analysis={},
        test_run={"passed": False, "summary": {}},
        heal_attempts=0,
        test_file="tests/generated_test.py",
        pipeline_events=events,
    )

    assert report["metrics"]["durations_seconds"]["analysis"] == 9.0
