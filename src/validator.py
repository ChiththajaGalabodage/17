from __future__ import annotations

import ast
import builtins
from pathlib import Path
from typing import Any

from src.analyzer import analyze_code


_ALLOWED_NAMES = set(dir(builtins)) | {"pytest", "datetime", "Path", "tc"}


def validate_generated_test_code(
    test_code: str,
    source_path: Path,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate generated pytest code before execution.

    The validator rejects syntax errors and unresolved bare calls that do not
    exist in the target module's exported surface. This catches cases where a
    generated test references a symbol like process_refund that is not
    available through ``from module import *``.
    """
    issues: list[str] = []
    analysis = analysis or analyze_code(str(source_path))

    try:
        compile(test_code, "<generated>", "exec")
    except SyntaxError as error:
        issues.append(f"Syntax error: {error.msg} (line {error.lineno})")
        return {
            "passed": False,
            "issues": issues,
            "exported_names": _exported_names(source_path, analysis),
        }

    exported_names = _exported_names(source_path, analysis)
    unresolved_calls = _unresolved_call_names(test_code, exported_names)
    if unresolved_calls:
        issues.append(
            "Unresolved test references: " + ", ".join(sorted(unresolved_calls))
        )

    trivial_assertions = _trivial_assertion_issues(test_code)
    if trivial_assertions:
        issues.extend(trivial_assertions)

    return {
        "passed": not issues,
        "issues": issues,
        "exported_names": sorted(exported_names),
    }


def build_smoke_test_code(source_path: Path) -> str:
    module_name = source_path.stem
    return (
        "import pytest\n"
        f"from {module_name} import *\n\n"
        "def test_smoke():\n"
        "    assert True\n"
    )


def _exported_names(source_path: Path, analysis: dict[str, Any]) -> set[str]:
    exported: set[str] = set()
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except Exception:
        return {fn.get("name", "") for fn in analysis.get("functions", [])} | {
            cls.get("name", "") for cls in analysis.get("classes", [])
        }

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    value = node.value
                    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                        for item in value.elts:
                            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                                exported.add(item.value)
                    break

    if exported:
        return exported

    exported.update(fn.get("name", "") for fn in analysis.get("functions", []))
    exported.update(cls.get("name", "") for cls in analysis.get("classes", []))
    return {name for name in exported if name}


def _unresolved_call_names(test_code: str, exported_names: set[str]) -> set[str]:
    tree = ast.parse(test_code)
    defined_names = _defined_names(tree)
    unresolved: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name):
            continue

        name = func.id
        if name in defined_names or name in _ALLOWED_NAMES or name in exported_names:
            continue
        unresolved.add(name)

    return unresolved


def _defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)

    return names


def _trivial_assertion_issues(test_code: str) -> list[str]:
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return []

    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue

        test = node.test
        if isinstance(test, ast.Constant) and isinstance(test.value, bool):
            issues.append("Trivial assertion: assert literal boolean")
            continue

        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            continue

        left = ast.dump(test.left, include_attributes=False)
        right = ast.dump(test.comparators[0], include_attributes=False)
        if left != right:
            continue

        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is, ast.NotEq, ast.IsNot)):
            issues.append("Trivial assertion: self-comparison in assert statement")

    return issues