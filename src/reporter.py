import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(
    analysis: dict[str, Any],
    test_run: dict[str, Any],
    heal_attempts: int,
    test_file: str,
) -> dict[str, Any]:
    """Build a JSON-serializable pipeline report."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "analysis": analysis,
        "test_file": test_file,
        "heal_attempts": heal_attempts,
        "test_run": test_run,
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
