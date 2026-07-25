from src.healer import classify_failure, heal_test_bundle, preserves_oracle_strength


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls = 0

    def generate_content(self, **_kwargs) -> _Response:
        self.calls += 1
        return _Response(self.response_text)


class _FakeGenerator:
    can_use_ai = True
    model = "fake-model"

    def __init__(self, response_text: str) -> None:
        self._client = type("Client", (), {"models": _Models(response_text)})()


def test_product_failure_is_not_healed_or_weakened() -> None:
    original = (
        "import pytest\n"
        "from target_code import add\n\n"
        "def test_addition():\n"
        "    assert add(1, 2) == 3\n"
    )

    generator = _FakeGenerator(
        '{"test_code": ["def test_addition():", "    assert add(1, 2) is not None"]}'
    )
    result = heal_test_bundle(
        current_test_code=original,
        test_output="FAILED test_generated.py::test_addition - AssertionError: assert -1 == 3",
        analysis={"functions": [{"name": "add"}]},
        ai_generator=generator,
    )

    assert result["action"] == "not-healed"
    assert result["failure_classification"]["category"] == "possible-product-defect"
    assert result["failure_classification"]["safe_to_heal"] is False
    assert result["test_code"] == original
    assert "assert add(1, 2) == 3" in result["test_code"]
    assert "is not None" not in result["test_code"]
    assert generator._client.models.calls == 0


def test_deterministic_artifact_repair_preserves_test_oracle() -> None:
    original = (
        "from target_code import subtract\n\n"
        "def test_subtraction():\n"
        "    assert subtract(7, 2) == 5\n"
    )

    result = heal_test_bundle(
        current_test_code=original,
        test_output="NameError: name 'pytest' is not defined",
        analysis={"functions": [{"name": "subtract"}]},
    )

    assert result["action"] == "repaired-import"
    assert result["test_code"] == "import pytest\n" + original
    assert "assert subtract(7, 2) == 5" in result["test_code"]
    assert "is not None" not in result["test_code"]


def test_mixed_artifact_and_product_failure_is_not_safe_to_heal() -> None:
    classification = classify_failure(
        "NameError: missing_fixture\nAssertionError: assert 2 == 3"
    )

    assert classification["category"] == "possible-product-defect"
    assert classification["safe_to_heal"] is False


def test_name_error_inside_product_code_is_not_safe_to_heal() -> None:
    classification = classify_failure(
        "target_code.py:12: NameError: name 'internal_state' is not defined"
    )

    assert classification["category"] == "unknown"
    assert classification["safe_to_heal"] is False


def test_ai_artifact_repair_cannot_weaken_expected_value() -> None:
    original = (
        "import pytest\n"
        "from target_code import subtract\n\n"
        "def test_subtraction():\n"
        "    assert subtract(7, 2) == 5\n"
    )
    generator = _FakeGenerator(
        '{"test_code": ["import pytest", "from target_code import subtract", "", '
        '"def test_subtraction():", "    assert subtract(7, 2) is not None"], '
        '"explanation": ["weakened oracle"]}'
    )

    result = heal_test_bundle(
        current_test_code=original,
        test_output="NameError: name 'pytest' is not defined",
        analysis={"functions": [{"name": "subtract"}]},
        ai_generator=generator,
    )

    assert "assert subtract(7, 2) == 5" in result["test_code"]
    assert "is not None" not in result["test_code"]


def test_ai_artifact_repair_cannot_replace_expected_value() -> None:
    original = (
        "import pytest\n"
        "from target_code import subtract\n\n"
        "def test_subtraction():\n"
        "    assert subtract(7, 2) == 5\n"
    )
    generator = _FakeGenerator(
        '{"test_code": ["import pytest", "from target_code import subtract", "", '
        '"def test_subtraction():", "    assert subtract(7, 2) == 999"], '
        '"explanation": ["changed expected value"]}'
    )

    result = heal_test_bundle(
        current_test_code=original,
        test_output="NameError: name 'pytest' is not defined",
        analysis={"functions": [{"name": "subtract"}]},
        ai_generator=generator,
    )

    assert "assert subtract(7, 2) == 5" in result["test_code"]
    assert "== 999" not in result["test_code"]


def test_ai_artifact_repair_cannot_change_setup_call() -> None:
    original = (
        "from target_code import add, subtract\n\n"
        "def test_calculation():\n"
        "    result = add(1, 2)\n"
        "    assert result == 3\n"
    )
    generator = _FakeGenerator(
        '{"test_code": ["import pytest", "from target_code import add, subtract", "", '
        '"def test_calculation():", "    result = subtract(1, 2)", "    assert result == 3"], '
        '"explanation": ["changed setup"]}'
    )

    result = heal_test_bundle(
        current_test_code=original,
        test_output="NameError: name 'pytest' is not defined",
        analysis={"functions": [{"name": "add"}, {"name": "subtract"}]},
        ai_generator=generator,
    )

    assert result["action"] == "rejected-oracle-weakening"
    assert "result = add(1, 2)" in result["test_code"]
    assert "result = subtract(1, 2)" not in result["test_code"]


def test_unparsable_original_cannot_bypass_semantic_guard() -> None:
    assert preserves_oracle_strength(
        "def test_broken(:\n    assert add(1, 2) == 3\n",
        "import pytest\n\ndef test_broken():\n    assert add(1, 2) == 3\n",
    ) is False
