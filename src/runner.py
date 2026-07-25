import subprocess
import sys
import time
import re
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _find_repo_root(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if (candidate / "main.py").exists() and (candidate / "src").is_dir():
            return candidate
    return path.parent


def run_pytest_targets(
    test_targets: list[str],
    *,
    timeout_seconds: float = 60.0,
    isolated: bool = False,
) -> dict[str, Any]:
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
            "timed_out": False,
            "summary": parse_pytest_summary(""),
            "isolated": isolated,
            "isolation_level": (
                "temporary-filesystem-copy-and-environment-scrubbing"
                if isolated
                else "none"
            ),
            "security_boundary": False,
        }

    normalized_targets = [str(Path(target.partition("::")[0])) + ("::" + target.partition("::")[2] if "::" in target else "") for target in test_targets]
    first_file = Path(normalized_targets[0].partition("::")[0]).resolve()
    repo_root = _find_repo_root(first_file)
    command_targets = _relative_test_targets(normalized_targets, repo_root)
    command = [sys.executable, "-m", "pytest", *command_targets, "-q"]

    start_time_iso = datetime.now(timezone.utc).isoformat()
    start_ts = time.time()

    allowed_environment = {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TZ",
        "WINDIR",
    }
    safe_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed_environment
    }
    safe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    safe_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    timed_out = False
    isolation_copy_seconds = 0.0
    temporary: tempfile.TemporaryDirectory | None = None
    execution_root = repo_root
    try:
        if isolated:
            copy_started = time.time()
            temporary = tempfile.TemporaryDirectory(prefix="generated-test-isolation-")
            isolation_root = Path(temporary.name)
            execution_root = isolation_root / "project"
            shutil.copytree(repo_root, execution_root, ignore=_copy_ignore)
            isolation_copy_seconds = round(time.time() - copy_started, 3)
            isolated_home = isolation_root / "home"
            isolated_temp = isolation_root / "tmp"
            isolated_home.mkdir()
            isolated_temp.mkdir()
            safe_env.update(
                {
                    "HOME": str(isolated_home),
                    "USERPROFILE": str(isolated_home),
                    "TMP": str(isolated_temp),
                    "TEMP": str(isolated_temp),
                    "TMPDIR": str(isolated_temp),
                    "PYTHONPATH": str(execution_root),
                }
            )
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                cwd=str(execution_root),
                timeout=max(float(timeout_seconds), 0.1),
                env=safe_env,
            )
            return_code = completed.returncode
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
        except subprocess.TimeoutExpired as error:
            timed_out = True
            return_code = 124
            stdout = _decode_timeout_stream(error.stdout)
            stderr = _decode_timeout_stream(error.stderr)
    finally:
        if temporary is not None:
            temporary.cleanup()

    end_ts = time.time()
    end_time_iso = datetime.now(timezone.utc).isoformat()
    duration = round(end_ts - start_ts, 3)

    output = "\n".join(
        chunk for chunk in [stdout, stderr] if chunk
    )
    if timed_out:
        output = (output + "\n" if output else "") + (
            f"Test execution timed out after {max(float(timeout_seconds), 0.1):.1f}s"
        )

    summary = parse_pytest_summary(output)
    return {
        "command": " ".join(command),
        "return_code": return_code,
        "passed": return_code == 0,
        "output": output,
        "start_time_utc": start_time_iso,
        "end_time_utc": end_time_iso,
        "duration_seconds": duration,
        "timed_out": timed_out,
        "isolated": isolated,
        "isolation_level": (
            "temporary-filesystem-copy-and-environment-scrubbing"
            if isolated
            else "none"
        ),
        "security_boundary": False,
        "isolation_copy_seconds": isolation_copy_seconds,
        "summary": summary,
    }


def run_pytest(
    test_file: str,
    *,
    timeout_seconds: float = 60.0,
    isolated: bool = False,
) -> dict[str, Any]:
    """Execute pytest for the provided test file and return result metadata."""
    return run_pytest_targets(
        [test_file], timeout_seconds=timeout_seconds, isolated=isolated
    )


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return value.strip()


def parse_pytest_summary(output: str) -> dict[str, Any]:
    """Parse stable, research-relevant fields from pytest text output."""
    labels = ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed")
    counts: dict[str, int] = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    for label in labels:
        matches = re.findall(rf"(\d+)\s+{label}\b", output, flags=re.IGNORECASE)
        if not matches:
            continue
        key = "errors" if label in {"error", "errors"} else label
        counts[key] = max(counts[key], int(matches[-1]))

    node_ids = sorted(
        set(
            re.findall(
                r"^(?:FAILED|ERROR)\s+([^\s]+(?:::[^\s]+)*)",
                output,
                flags=re.MULTILINE,
            )
        )
    )
    counts["total"] = sum(counts.values())
    return {**counts, "failing_node_ids": node_ids}


def run_stability(
    test_targets: list[str],
    runs: int = 3,
    *,
    isolated: bool = False,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Repeat a suite and report outcome consistency without hiding failures."""
    run_count = max(1, int(runs))
    observations = [
        run_pytest_targets(
            test_targets,
            isolated=isolated,
            timeout_seconds=timeout_seconds,
        )
        for _ in range(run_count)
    ]
    signatures = [
        {
            "return_code": result["return_code"],
            "passed": result["passed"],
            "passed_count": result["summary"]["passed"],
            "failed_count": result["summary"]["failed"],
            "error_count": result["summary"]["errors"],
            "failing_node_ids": result["summary"]["failing_node_ids"],
        }
        for result in observations
    ]
    unique_signatures = {json_signature(item) for item in signatures}
    return {
        "runs": run_count,
        "consistent": len(unique_signatures) == 1,
        "flaky": len(unique_signatures) > 1,
        "all_passed": all(result["passed"] for result in observations),
        "unique_outcome_count": len(unique_signatures),
        "signatures": signatures,
        "durations_seconds": [result["duration_seconds"] for result in observations],
    }


def json_signature(value: dict[str, Any]) -> str:
    """Create a deterministic signature without importing reporting code."""
    parts = [
        str(value.get("return_code")),
        str(value.get("passed")),
        str(value.get("passed_count")),
        str(value.get("failed_count")),
        str(value.get("error_count")),
        ",".join(value.get("failing_node_ids", [])),
    ]
    return "|".join(parts)


def _relative_test_targets(targets: list[str], repo_root: Path) -> list[str]:
    normalized: list[str] = []
    for target in targets:
        file_part, separator, node_part = target.partition("::")
        absolute = Path(file_part).resolve()
        try:
            relative = absolute.relative_to(repo_root).as_posix()
        except ValueError as error:
            raise ValueError(f"Test target must stay inside repository: {target}") from error
        normalized.append(relative + (f"::{node_part}" if separator else ""))
    return normalized


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored_names = {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "venv",
    }
    parent = Path(directory)
    return {
        name
        for name in names
        if name in ignored_names
        or name == ".env"
        or name.startswith(".env.")
        or (parent / name).is_symlink()
    }
