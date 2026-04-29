from pathlib import Path

from src.generator import GeminiTestGenerator
from src.output_format import normalize_test_code, parse_generation_bundle


def test_parse_generation_bundle_extracts_code_and_explanation() -> None:
    raw_output = (
        '{"test_code": ["import pytest", "from target_code import *", "", "def test_example():", "    assert True"], '
        '"explanation": ["Covers the import path.", "Uses a smoke test for the module surface."]}'
    )

    bundle = parse_generation_bundle(raw_output)

    assert bundle["test_code"].startswith("import pytest")
    assert bundle["explanation"] == [
        "Covers the import path.",
        "Uses a smoke test for the module surface.",
    ]


def test_normalize_test_code_rebuilds_import_block() -> None:
    normalized = normalize_test_code(
        "import pytest\nfrom target_code import *\n\ndef test_example():\n    assert True\n",
        Path("target_code.py"),
    )

    assert normalized.startswith("import pytest\nfrom target_code import *\n\n")
    assert "def test_example():" in normalized


def test_fallback_generation_includes_explanation() -> None:
    generator = GeminiTestGenerator(api_key=None)

    bundle = generator._generate_fallback(
        source="def sample(value):\n    return value\n",
        analysis={
            "file": "sample.py",
            "function_count": 1,
            "class_count": 0,
            "functions": [{"name": "sample", "args": ["value"], "has_docstring": False, "line": 1}],
            "classes": [],
        },
    )

    assert "def test_sample_basic()" in bundle["test_code"]
    assert bundle["explanation"]