from pathlib import Path

from src.analyzer import analyze_code
from src.validator import validate_generated_test_code


def test_validate_generated_test_code_accepts_exported_calls() -> None:
    source_path = Path("target_code.py")
    result = validate_generated_test_code(
        "import pytest\nfrom target_code import *\n\ndef test_smoke():\n    reset_demo_state()\n",
        source_path,
        analyze_code(str(source_path)),
    )

    assert result["passed"] is True
    assert result["issues"] == []


def test_validate_generated_test_code_flags_unexported_calls() -> None:
    source_path = Path("target_code.py")
    result = validate_generated_test_code(
        "import pytest\nfrom target_code import *\n\ndef test_refund():\n    process_refund(1, 2)\n",
        source_path,
        analyze_code(str(source_path)),
    )

    assert result["passed"] is False
    assert any("process_refund" in issue for issue in result["issues"])