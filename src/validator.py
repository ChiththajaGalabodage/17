from __future__ import annotations

import ast
import builtins
from collections import Counter
from pathlib import Path
from typing import Any

from src.analyzer import analyze_code


_ALLOWED_NAMES = set(dir(builtins)) | {"pytest", "datetime", "Path", "tc"}


def validate_generated_test_code(
    test_code: str,
    source_path: Path,
    analysis: dict[str, Any] | None = None,
    *,
    minimum_target_coverage: float = 0.0,
) -> dict[str, Any]:
    """Validate generated pytest code before execution.

    The result intentionally separates hard ``issues`` from diagnostic
    ``warnings`` and exposes raw quality metrics.  This prevents a syntactically
    valid but meaningless suite (for example ``assert True`` or only
    ``is not None`` checks) from being counted as successful research output.
    ``minimum_target_coverage`` is the percentage of public top-level
    callables that must be exercised by at least one generated test.
    """
    issues: list[str] = []
    warnings: list[str] = []
    analysis = analysis or analyze_code(str(source_path))

    try:
        tree = ast.parse(test_code, filename="<generated>")
        compile(tree, "<generated>", "exec")
    except SyntaxError as error:
        issues.append(f"Syntax error: {error.msg} (line {error.lineno})")
        return {
            "passed": False,
            "issues": issues,
            "warnings": warnings,
            "exported_names": sorted(_exported_names(source_path, analysis)),
            "metrics": _empty_quality_metrics(),
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

    duplicate_names = _duplicate_test_names(tree)
    if duplicate_names:
        issues.append("Duplicate test names: " + ", ".join(duplicate_names))

    issues.extend(_security_issues(tree))

    metrics, per_test_issues, per_test_warnings = _quality_metrics(
        tree,
        exported_names=exported_names,
        analysis=analysis,
    )
    issues.extend(per_test_issues)
    warnings.extend(per_test_warnings)

    target_coverage = float(metrics["target_function_coverage_percent"])
    if minimum_target_coverage > 0 and target_coverage < minimum_target_coverage:
        issues.append(
            "Target callable coverage below quality gate: "
            f"{target_coverage:.2f}% < {minimum_target_coverage:.2f}%"
        )

    return {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "exported_names": sorted(exported_names),
        "metrics": metrics,
    }


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
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)

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
            issues.append(f"Trivial assertion at line {node.lineno}: literal boolean")
            continue

        if _is_none_check(test):
            issues.append(
                f"Weak assertion at line {node.lineno}: a non-None check does not verify behaviour"
            )
            continue

        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "callable"
        ):
            issues.append(
                f"Weak assertion at line {node.lineno}: callable() does not verify behaviour"
            )
            continue

        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            continue

        left = ast.dump(test.left, include_attributes=False)
        right = ast.dump(test.comparators[0], include_attributes=False)
        if left != right:
            continue

        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is, ast.NotEq, ast.IsNot)):
            issues.append(
                f"Trivial assertion at line {node.lineno}: self-comparison"
            )

    return issues


def _is_none_check(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    comparator = node.comparators[0]
    return isinstance(node.ops[0], ast.IsNot) and isinstance(
        comparator, ast.Constant
    ) and comparator.value is None


def _duplicate_test_names(tree: ast.Module) -> list[str]:
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    return sorted(name for name, count in Counter(names).items() if count > 1)


def _quality_metrics(
    tree: ast.Module,
    *,
    exported_names: set[str],
    analysis: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    test_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if not test_nodes:
        issues.append("No pytest test functions were generated")

    assertion_count = 0
    exception_assertion_count = 0
    tests_without_assertions: list[str] = []
    covered_targets: set[str] = set()

    for test_node in test_nodes:
        assertions = [node for node in ast.walk(test_node) if isinstance(node, ast.Assert)]
        raises_calls = [
            node
            for node in ast.walk(test_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pytest"
            and node.func.attr == "raises"
        ]
        assertion_count += len(assertions)
        exception_assertion_count += len(raises_calls)
        if not assertions and not raises_calls:
            tests_without_assertions.append(test_node.name)

        for node in ast.walk(test_node):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in exported_names:
                covered_targets.add(node.func.id)
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"tc", "sut"}
                and node.func.attr in exported_names
            ):
                covered_targets.add(node.func.attr)

    for test_name in tests_without_assertions:
        issues.append(f"Test {test_name} has no behavioural assertion")

    public_targets = {
        str(item.get("name"))
        for item in analysis.get("functions", [])
        if item.get("name")
        and not str(item.get("name")).startswith("_")
        and item.get("name") != "main"
    }
    public_targets.update(
        str(item.get("name"))
        for item in analysis.get("classes", [])
        if item.get("name") and not str(item.get("name")).startswith("_")
    )
    public_targets.discard("")
    covered_public_targets = public_targets & covered_targets
    uncovered_targets = sorted(public_targets - covered_public_targets)
    coverage_percent = (
        len(covered_public_targets) / len(public_targets) * 100.0
        if public_targets
        else 100.0
    )
    if uncovered_targets:
        warnings.append("Public callables not exercised: " + ", ".join(uncovered_targets))

    metrics = {
        "test_function_count": len(test_nodes),
        "assertion_count": assertion_count,
        "exception_assertion_count": exception_assertion_count,
        "tests_without_assertions": tests_without_assertions,
        "target_calls": sorted(covered_targets),
        "public_target_count": len(public_targets),
        "covered_public_targets": sorted(covered_public_targets),
        "uncovered_public_targets": uncovered_targets,
        "target_function_coverage_percent": round(coverage_percent, 2),
        "quality_signature": [
            assertion_count + exception_assertion_count,
            len(covered_public_targets),
            len(test_nodes),
        ],
    }
    return metrics, issues, warnings


def _empty_quality_metrics() -> dict[str, Any]:
    return {
        "test_function_count": 0,
        "assertion_count": 0,
        "exception_assertion_count": 0,
        "tests_without_assertions": [],
        "target_calls": [],
        "public_target_count": 0,
        "covered_public_targets": [],
        "uncovered_public_targets": [],
        "target_function_coverage_percent": 0.0,
        "quality_signature": [0, 0, 0],
    }


def _security_issues(tree: ast.Module) -> list[str]:
    """Reject high-risk operations before generated code is executed."""
    issues: list[str] = []
    prohibited_modules = {
        "subprocess",
        "socket",
        "requests",
        "http",
        "urllib",
        "ftplib",
        "shutil",
    }
    prohibited_bare_calls = {
        "__import__",
        "eval",
        "exec",
        "compile",
        "open",
        "input",
    }
    prohibited_attributes = {
        "system",
        "popen",
        "Popen",
        "check_call",
        "check_output",
        "remove",
        "unlink",
        "rmdir",
        "rmtree",
        "write_text",
        "write_bytes",
        "rename",
        "connect",
        "urlopen",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in prohibited_modules:
                    issues.append(
                        f"Unsafe import at line {node.lineno}: {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in prohibited_modules:
                issues.append(
                    f"Unsafe import at line {node.lineno}: {node.module}"
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in prohibited_bare_calls:
                issues.append(
                    f"Unsafe call at line {node.lineno}: {node.func.id}()"
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in prohibited_attributes
            ):
                issues.append(
                    f"Unsafe call at line {node.lineno}: .{node.func.attr}()"
                )

    return sorted(set(issues))
