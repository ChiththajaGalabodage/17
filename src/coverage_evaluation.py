"""Isolated line and branch coverage evaluation for one source module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from src.output_format import module_name_from_path


def evaluate_coverage(
    source_path: str | Path,
    test_targets: Sequence[str | Path],
    *,
    project_root: str | Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run coverage.py in a temporary project copy and return raw counts."""
    root = Path(project_root).resolve()
    source = _inside((root / source_path).resolve(), root, "source")
    if not source.is_file():
        raise FileNotFoundError(f"Coverage source not found: {source_path}")
    if not test_targets:
        raise ValueError("At least one coverage test target is required")
    relative_source = source.relative_to(root)
    normalized_targets: list[str] = []
    for raw in test_targets:
        text = os.fspath(raw)
        file_part, separator, node_part = text.partition("::")
        target = _inside((root / file_part).resolve(), root, "test target")
        if not target.exists():
            raise FileNotFoundError(f"Coverage test target not found: {file_part}")
        normalized = target.relative_to(root).as_posix()
        normalized_targets.append(normalized + (f"::{node_part}" if separator else ""))

    with tempfile.TemporaryDirectory(prefix="coverage-eval-") as temporary:
        isolation = Path(temporary)
        copied = isolation / "project"
        shutil.copytree(root, copied, ignore=_copy_ignore)
        home = isolation / "home"
        temp = isolation / "tmp"
        home.mkdir()
        temp.mkdir()
        data_file = isolation / ".coverage"
        json_file = isolation / "coverage.json"
        environment = _environment(copied, home, temp, data_file)
        module_name = module_name_from_path(relative_source)
        command = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            f"--source={module_name}",
            "-m",
            "pytest",
            "-q",
            *normalized_targets,
        ]
        started = time.monotonic()
        run = subprocess.run(
            command,
            cwd=copied,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        json_command = [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "-o",
            str(json_file),
        ]
        json_run = subprocess.run(
            json_command,
            cwd=copied,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = round(time.monotonic() - started, 3)
        if json_run.returncode != 0 or not json_file.is_file():
            return {
                "valid": False,
                "reason": "coverage-json-failed",
                "test_return_code": run.returncode,
                "coverage_return_code": json_run.returncode,
                "stdout": run.stdout,
                "stderr": "\n".join(part for part in (run.stderr, json_run.stderr) if part),
                "duration_seconds": duration,
            }
        payload = json.loads(json_file.read_text(encoding="utf-8"))
        files = payload.get("files", {})
        source_key = _find_source_key(files, relative_source)
        if source_key is None:
            return {
                "valid": False,
                "reason": "source-missing-from-coverage-json",
                "available_files": sorted(files),
                "duration_seconds": duration,
            }
        summary = files[source_key].get("summary", {})
        statements = int(summary.get("num_statements", 0) or 0)
        covered_lines = int(summary.get("covered_lines", 0) or 0)
        branches = int(summary.get("num_branches", 0) or 0)
        covered_branches = int(summary.get("covered_branches", 0) or 0)
        return {
            "valid": True,
            "source": relative_source.as_posix(),
            "test_targets": normalized_targets,
            "tests_passed": run.returncode == 0,
            "test_return_code": run.returncode,
            "num_statements": statements,
            "covered_lines": covered_lines,
            "missing_lines": int(summary.get("missing_lines", 0) or 0),
            "line_coverage_percent": round(
                covered_lines / statements * 100.0 if statements else 100.0, 2
            ),
            "num_branches": branches,
            "covered_branches": covered_branches,
            "missing_branches": int(summary.get("missing_branches", 0) or 0),
            "branch_coverage_percent": round(
                covered_branches / branches * 100.0 if branches else 100.0, 2
            ),
            "duration_seconds": duration,
            "coverage_schema_version": payload.get("meta", {}).get("version"),
        }


def _find_source_key(files: dict[str, Any], relative_source: Path) -> str | None:
    wanted = relative_source.as_posix().lower()
    for key in files:
        normalized = key.replace("\\", "/").lower()
        if normalized == wanted or normalized.endswith("/" + wanted):
            return key
    return None


def _inside(path: Path, root: Path, label: str) -> Path:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Coverage {label} must remain inside project root") from error
    return path


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored_names = {
        ".git",
        ".hg",
        ".svn",
        ".pytest_cache",
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


def _environment(project: Path, home: Path, temp: Path, data_file: Path) -> dict[str, str]:
    allowed = {"COMSPEC", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TZ", "WINDIR"}
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMP": str(temp),
            "TEMP": str(temp),
            "TMPDIR": str(temp),
            "COVERAGE_FILE": str(data_file),
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(project),
        }
    )
    return environment


__all__ = ["evaluate_coverage"]
