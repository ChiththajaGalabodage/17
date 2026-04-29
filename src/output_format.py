from __future__ import annotations

import json
import re
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
    cleaned = strip_code_fences(test_code)

    module_name = source_path.stem
    body_lines: list[str] = []

    for raw_line in cleaned.splitlines():
        line = raw_line.rstrip()

        line = re.sub(r"import\s+pytest\b.*", "", line)
        line = re.sub(rf"from\s+{re.escape(module_name)}\s+import\b.*", "", line)
        line = re.sub(rf"import\s+{re.escape(module_name)}\b.*", "", line)

        if line.strip():
            body_lines.append(line.rstrip())

    normalized_lines = ["import pytest", f"from {module_name} import *", ""]
    normalized_lines.extend(body_lines)

    return "\n".join(normalized_lines).rstrip() + "\n"


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