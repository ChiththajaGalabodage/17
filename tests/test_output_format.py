from pathlib import Path

from src.generator import GeminiTestGenerator
from src.output_format import normalize_test_code, parse_generation_bundle


def test_build_prompt_contains_strict_quality_rules() -> None:
    generator = GeminiTestGenerator(api_key=None)

    prompt = generator._build_prompt(
        source="def sample(value):\n    return value\n",
        analysis={"file": "sample.py", "functions": [], "classes": []},
    )
    assert "You are a senior Python test engineer." in prompt
    assert 'DO NOT use "assert result is not None"' in prompt
    assert "Every test must validate real expected outputs" in prompt
    assert "at least 1 normal case" in prompt
    assert "at least 1 failure case (use pytest.raises)" in prompt


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
        source=Path("target_code.py").read_text(encoding="utf-8"),
        analysis={
            "file": "target_code.py",
            "function_count": 14,
            "class_count": 0,
            "functions": [
                {"name": "_to_int", "args": ["value", "default"], "has_docstring": False, "line": 1},
                {"name": "_to_price", "args": ["value", "default"], "has_docstring": False, "line": 1},
                {"name": "_to_text", "args": ["value", "default"], "has_docstring": False, "line": 1},
                {"name": "_utc_now", "args": [], "has_docstring": False, "line": 1},
                {"name": "reset_demo_state", "args": [], "has_docstring": True, "line": 1},
                {"name": "initialize_store", "args": ["seed"], "has_docstring": True, "line": 1},
                {"name": "register_customer", "args": ["customer_id", "name"], "has_docstring": True, "line": 1},
                {"name": "upsert_inventory", "args": ["product_id", "quantity"], "has_docstring": True, "line": 1},
                {"name": "add_to_cart", "args": ["customer_id", "product_id", "quantity"], "has_docstring": True, "line": 1},
                {"name": "create_order", "args": ["customer_id", "shipping_fee"], "has_docstring": True, "line": 1},
                {"name": "calculate_order_total", "args": ["order_id", "include_tax"], "has_docstring": True, "line": 1},
                {"name": "cancel_order", "args": ["order_id", "reason"], "has_docstring": True, "line": 1},
                {"name": "get_customer_history", "args": ["customer_id"], "has_docstring": True, "line": 1},
                {"name": "generate_sales_report", "args": ["start_order_id", "end_order_id"], "has_docstring": True, "line": 1},
                {"name": "process_refund", "args": ["order_id", "amount"], "has_docstring": True, "line": 1},
            ],
            "classes": [],
        },
    )

    assert "assert result is not None" not in bundle["test_code"]
    assert "pytest.raises" in bundle["test_code"]
    assert "assert order['status'] == 'confirmed'" in bundle["test_code"]
    assert "def test_process_refund_behaves_consistently():" in bundle["test_code"]
    assert bundle["explanation"]