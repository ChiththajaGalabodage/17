import subprocess
import sys
from pathlib import Path
from typing import Any


def run_pytest(test_file: str) -> dict[str, Any]:
    """Execute pytest for the provided test file and return result metadata."""
    path = Path(test_file)
    command = [sys.executable, "-m", "pytest", str(path), "-q"]

    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        cwd=str(path.parent.parent),
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
