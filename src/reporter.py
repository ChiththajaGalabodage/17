import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(
    analysis: dict[str, Any],
    test_run: dict[str, Any],
    heal_attempts: int,
    test_file: str,
    pipeline_events: list[dict[str, Any]] | None = None,
    predictive_selection: dict[str, Any] | None = None,
    heal_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable pipeline report."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "analysis": analysis,
        "test_file": test_file,
        "heal_attempts": heal_attempts,
        "test_run": test_run,
        "pipeline_events": pipeline_events or [],
        "predictive_selection": predictive_selection or {
            "enabled": False,
            "changed_files": [],
            "selected_tests": [],
        },
        "heal_history": heal_history or [],
        "metrics": {
            "functions": analysis.get("function_count", 0),
            "classes": analysis.get("class_count", 0),
            "passed": bool(test_run.get("passed", False)),
        },
    }


def write_report(report: dict[str, Any], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
