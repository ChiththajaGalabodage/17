from __future__ import annotations

import json
import re
import ast
from pathlib import Path
from typing import Any


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```python"):
        cleaned = cleaned[len("```python") :].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```") :].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def parse_generation_bundle(raw_output: str) -> dict[str, Any]:
    cleaned = strip_code_fences(raw_output)

    try:
        payload = json.loads(cleaned)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        return {
            "test_code": _coerce_test_code(payload.get("test_code", "")),
            "explanation": _coerce_explanation(payload.get("explanation", [])),
        }

    return {
        "test_code": cleaned,
        "explanation": [],
    }


def normalize_test_code(test_code: str, source_path: Path) -> str:
    """Normalize imports without hiding invalid model output.

    Earlier versions replaced syntactically invalid generations with
    ``assert True``.  That made a failed generation look successful and
    invalidated the experiment metrics.  Syntax and semantic rejection now
    belongs to :mod:`src.validator`; this function only normalizes imports.
    """
    cleaned = strip_code_fences(test_code)

    module_name = module_name_from_path(source_path)
    body_lines: list[str] = []
    preserved_imports: list[str] = []

    for raw_line in cleaned.splitlines():
        line = raw_line.rstrip()

        stripped = line.strip()
        if re.fullmatch(r"import\s+pytest(?:\s+as\s+\w+)?", stripped):
            continue
        if re.match(rf"from\s+{re.escape(module_name)}\s+import\b", stripped):
            continue
        if re.fullmatch(
            rf"import\s+{re.escape(module_name)}(?:\s+as\s+\w+)?",
            stripped,
        ):
            continue
        if stripped.startswith(("import ", "from ")):
            if stripped not in preserved_imports:
                preserved_imports.append(stripped)
            continue

        if stripped:
            body_lines.append(line.rstrip())

    uses_tc_alias = any(re.search(r"\btc\.", line) for line in body_lines)
    private_imports = _referenced_private_callables(body_lines, source_path)

    normalized_lines = ["import pytest", f"from {module_name} import *"]
    if uses_tc_alias:
        normalized_lines.append(f"import {module_name} as tc")
    if private_imports:
        normalized_lines.append(
            f"from {module_name} import {', '.join(sorted(private_imports))}"
        )
    normalized_lines.extend(preserved_imports)
    normalized_lines.append("")
    normalized_lines.extend(body_lines)

    return "\n".join(normalized_lines).rstrip() + "\n"


def _referenced_private_callables(body_lines: list[str], source_path: Path) -> set[str]:
    """Return private module callables referenced as bare names in tests."""
    try:
        source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        test_tree = ast.parse("\n".join(body_lines))
    except (OSError, SyntaxError, UnicodeError):
        return set()

    private_names = {
        node.name
        for node in source_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name.startswith("_")
        and not node.name.startswith("__")
    }
    called_names = {
        node.func.id
        for node in ast.walk(test_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return private_names & called_names


def module_name_from_path(source_path: Path) -> str:
    """Return an importable dotted module name for a repository-relative file."""
    path = source_path.with_suffix("")
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd().resolve())
        except ValueError:
            return source_path.stem
    parts = list(path.parts)
    if parts and all(part.isidentifier() for part in parts):
        return ".".join(parts)
    return source_path.stem


def build_fallback_explanation(analysis: dict[str, Any]) -> list[str]:
    function_count = analysis.get("function_count", 0)
    class_count = analysis.get("class_count", 0)
    explanation = [
        f"Covers {function_count} function(s) and {class_count} class(es) discovered in the target module.",
        "Uses simple call-or-assert patterns so the tests stay executable and easy to heal.",
    ]
    if function_count == 0 and class_count == 0:
        explanation.append("Falls back to an import smoke test when no callable surface is detected.")
    return explanation


def _coerce_test_code(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value).strip()
    return str(value).strip()


def _coerce_explanation(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        lines = [line.strip("-• \t") for line in value.splitlines()]
        return [line for line in lines if line]
    if value is None:
        return []
    return [str(value).strip()]
