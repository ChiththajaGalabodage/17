#!/usr/bin/env python3
"""Validate generated test files for syntax errors before running pytest.

This script looks for files under `tests/` whose filename contains the
word "generated" (case-insensitive) and attempts to parse them with the
AST module. If parsing fails, it prints the error and exits with non-zero
status so CI can stop early before pytest runs.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def find_generated_tests(root: Path) -> list[Path]:
    return [p for p in root.glob("**/*.py") if "generated" in p.name.lower()]


def validate_file(path: Path) -> tuple[bool, str]:
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"I/O error: {exc}"
    try:
        tree = ast.parse(src, filename=str(path))
        alias_issue = _check_tc_alias_usage(tree)
        if alias_issue:
            return False, alias_issue
        return True, "OK"
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} (line {exc.lineno})"
    except Exception as exc:  # pragma: no cover - unexpected parse issue
        return False, f"Parse error: {exc}"


def _check_tc_alias_usage(tree: ast.AST) -> str | None:
    uses_tc = False
    has_tc_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "target_code" and alias.asname == "tc":
                    has_tc_import = True
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "tc":
                uses_tc = True

    if uses_tc and not has_tc_import:
        return "NameError risk: found 'tc.' usage without 'import target_code as tc'"
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tests_dir = root / "tests"
    if not tests_dir.exists():
        print("No tests/ directory found; skipping generated test validation.")
        return 0

    files = find_generated_tests(tests_dir)
    if not files:
        print("No generated test files found; skipping.")
        return 0

    failed = 0
    for f in files:
        ok, msg = validate_file(f)
        if ok:
            print(f"[OK] {f}")
        else:
            failed += 1
            print(f"[ERROR] {f}: {msg}")

    if failed:
        print(f"{failed} generated test file(s) failed validation.")
        return 2
    print("All generated test files validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
