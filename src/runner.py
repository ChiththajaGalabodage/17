import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _find_repo_root(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if (candidate / "main.py").exists() and (candidate / "src").is_dir():
            return candidate
    return path.parent


def run_pytest_targets(test_targets: list[str]) -> dict[str, Any]:
    """Execute pytest for one or more test targets and return metadata."""
    if not test_targets:
        return {
            "command": "",
            "return_code": 0,
            "passed": True,
            "output": "No test targets were selected.",
            "start_time_utc": None,
            "end_time_utc": None,
            "duration_seconds": 0.0,
        }

    normalized_targets = [str(Path(target)) for target in test_targets]
    command = [sys.executable, "-m", "pytest", *normalized_targets, "-q"]

    start_time_iso = datetime.now(timezone.utc).isoformat()
    start_ts = time.time()

    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        cwd=str(_find_repo_root(Path(normalized_targets[0]).resolve())),
    )

    end_ts = time.time()
    end_time_iso = datetime.now(timezone.utc).isoformat()
    duration = round(end_ts - start_ts, 3)

    output = "\n".join(
        chunk for chunk in [completed.stdout.strip(), completed.stderr.strip()] if chunk
    )

    return {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "output": output,
        "start_time_utc": start_time_iso,
        "end_time_utc": end_time_iso,
        "duration_seconds": duration,
    }


def run_pytest(test_file: str) -> dict[str, Any]:
    """Execute pytest for the provided test file and return result metadata."""
    return run_pytest_targets([test_file])
