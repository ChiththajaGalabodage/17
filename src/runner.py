import subprocess
import sys
from pathlib import Path
from typing import Any


def run_pytest_targets(test_targets: list[str]) -> dict[str, Any]:
    """Execute pytest for one or more test targets and return metadata."""
    if not test_targets:
        return {
            "command": "",
            "return_code": 0,
            "passed": True,
            "output": "No test targets were selected.",
        }

    normalized_targets = [str(Path(target)) for target in test_targets]
    command = [sys.executable, "-m", "pytest", *normalized_targets, "-q"]

    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        cwd=str(Path(normalized_targets[0]).parent.parent),
    )

    output = "\n".join(
        chunk for chunk in [completed.stdout.strip(), completed.stderr.strip()] if chunk
    )

    return {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "output": output,
    }


def run_pytest(test_file: str) -> dict[str, Any]:
    """Execute pytest for the provided test file and return result metadata."""
    return run_pytest_targets([test_file])
