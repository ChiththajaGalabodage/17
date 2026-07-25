import time
import re
import ast
from collections import Counter
from typing import Any

from src.output_format import parse_generation_bundle


def heal_test_bundle(
    current_test_code: str,
    test_output: str,
    analysis: dict[str, Any],
    ai_generator: Any | None = None,
) -> dict[str, Any]:
    """Try to heal test code after a failing run.

    Healing is limited to defects in the generated test artifact.  A failing
    behavioural assertion or an exception raised by the system under test is
    evidence of a possible product defect and must not be erased by weakening
    the oracle.
    """
    classification = classify_failure(test_output)
    if not classification["safe_to_heal"]:
        return {
            "test_code": current_test_code,
            "explanation": [
                "No repair applied because the failure may represent a product defect.",
                str(classification["reason"]),
            ],
            "action": "not-healed",
            "failure_classification": classification,
        }

    if ai_generator is not None and getattr(ai_generator, "can_use_ai", False):
        prompt = (
            "The following generated pytest file has a verified missing-pytest-import defect. Add only `import pytest`. "
            "Never change an expected value, remove an assertion, replace an equality assertion with a weaker check, or change product code. "
            "If a safe repair is impossible, return the original test code unchanged. Return ONLY a JSON object with exactly these keys:\n"
            "{\n"
            '  "test_code": ["import pytest", "...pytest code lines..."],\n'
            '  "explanation": ["concise bullet 1", "concise bullet 2"]\n'
            "}\n\n"
            "Rules:\n"
            "1. test_code must be a JSON array of strings containing executable pytest code.\n"
            "2. explanation must be a JSON array of short bullets describing the fix.\n"
            "3. Do not include markdown fences, prose, or extra keys.\n\n"
            f"Failure output:\n{test_output}\n\n"
            f"Code analysis:\n{analysis}\n\n"
            f"Current tests:\n{current_test_code}"
        )
        max_retries = 3
        retry_delay_seconds = 5
        for attempt in range(1, max_retries + 1):
            try:
                if hasattr(ai_generator, "_api_calls"):
                    ai_generator._api_calls += 1
                response = ai_generator._client.models.generate_content(
                    model=ai_generator.model,
                    contents=prompt,
                )
                if hasattr(ai_generator, "record_api_response"):
                    ai_generator.record_api_response(response, phase="healing")
                bundle = parse_generation_bundle(response.text or "")
                healed = bundle["test_code"].strip()
                if healed:
                    if not preserves_oracle_strength(current_test_code, healed):
                        return {
                            "test_code": current_test_code,
                            "explanation": [
                                "Rejected AI repair because it weakened or removed a behavioural assertion."
                            ],
                            "action": "rejected-oracle-weakening",
                            "failure_classification": classification,
                        }
                    bundle["action"] = "candidate-repair"
                    bundle["failure_classification"] = classification
                    return bundle
                raise ValueError("Empty healing response from API")
            except Exception as error:
                print(f"Healer API Error (Attempt {attempt}/{max_retries}): {error}")
                if attempt < max_retries:
                    print(f"Retrying healer in {retry_delay_seconds} seconds...")
                    time.sleep(retry_delay_seconds)
                else:
                    print("Healer retries exhausted. Using local deterministic heal.")
                    break

    # The deterministic fallback performs only a semantics-preserving import
    # repair.  It deliberately leaves all assertions and expected values
    # untouched.
    healed = current_test_code
    action = "unchanged"
    explanation: list[str] = []
    if not re.search(r"^\s*import\s+pytest\b", healed, flags=re.MULTILINE):
        healed = "import pytest\n" + healed
        action = "repaired-import"
        explanation.append("Added the missing pytest import without changing test oracles.")
    else:
        explanation.append("No deterministic semantics-preserving repair was available.")
    return {
        "test_code": healed,
        "explanation": explanation,
        "action": action,
        "failure_classification": classification,
    }


def heal_test_code(
    current_test_code: str,
    test_output: str,
    analysis: dict[str, Any],
    ai_generator: Any | None = None,
) -> str:
    return heal_test_bundle(
        current_test_code=current_test_code,
        test_output=test_output,
        analysis=analysis,
        ai_generator=ai_generator,
    )["test_code"]


def classify_failure(test_output: str) -> dict[str, Any]:
    """Classify whether changing generated tests is defensible.

    This is intentionally conservative.  Mixed failures are treated as
    product evidence because automatically editing their assertions would
    create a false pass.
    """
    output = test_output or ""
    product_markers = (
        "AssertionError",
        "assert ",
        "ZeroDivisionError",
        "IndexError",
        "KeyError",
        "ValueError",
        "TypeError",
    )
    product_hits = sorted(marker for marker in product_markers if marker in output)
    missing_pytest = bool(
        re.search(
            r"NameError:\s+name\s+['\"]pytest['\"]\s+is\s+not\s+defined",
            output,
            flags=re.IGNORECASE,
        )
    )

    if product_hits:
        return {
            "category": "possible-product-defect",
            "safe_to_heal": False,
            "markers": product_hits,
            "reason": "Behavioural failures must remain visible to preserve defect-detection evidence.",
        }
    if missing_pytest:
        return {
            "category": "test-artifact-defect",
            "safe_to_heal": True,
            "markers": ["missing-pytest-import"],
            "reason": "Only adding the missing pytest import is permitted.",
        }
    return {
        "category": "unknown",
        "safe_to_heal": False,
        "markers": [],
        "reason": "Unknown failures require human review rather than automatic oracle changes.",
    }


def preserves_oracle_strength(before_code: str, after_code: str) -> bool:
    """Permit only a missing ``import pytest`` addition.

    Assertions alone are not a sufficient semantic fingerprint: changing a
    setup assignment or a call inside ``pytest.raises`` can silently alter the
    behaviour being tested.  Runtime healing therefore preserves every
    non-import statement exactly and rejects unparsable inputs.
    """
    try:
        before_tree = ast.parse(before_code)
        after_tree = ast.parse(after_code)
    except SyntaxError:
        return False

    before = _oracle_profile(before_tree)
    after = _oracle_profile(after_tree)
    return (
        after["assertions"] >= before["assertions"]
        and after["strong_assertions"] >= before["strong_assertions"]
        and after["raises"] >= before["raises"]
        and _oracle_fingerprints(before_tree) == _oracle_fingerprints(after_tree)
        and _non_import_fingerprints(before_tree)
        == _non_import_fingerprints(after_tree)
        and _only_missing_pytest_import_was_added(before_tree, after_tree)
    )


def _non_import_fingerprints(tree: ast.Module) -> list[str]:
    return [
        ast.dump(node, include_attributes=False)
        for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _only_missing_pytest_import_was_added(
    before_tree: ast.Module,
    after_tree: ast.Module,
) -> bool:
    before_imports = Counter(
        ast.dump(node, include_attributes=False)
        for node in before_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    after_imports = Counter(
        ast.dump(node, include_attributes=False)
        for node in after_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    additions = after_imports - before_imports
    removals = before_imports - after_imports
    allowed = ast.dump(ast.parse("import pytest").body[0], include_attributes=False)
    return not removals and set(additions) <= {allowed}


def _oracle_profile(tree: ast.AST) -> dict[str, int]:
    assertions = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    strong_assertions = 0
    for assertion in assertions:
        test = assertion.test
        weak = (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ) or (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "callable"
        ) or (
            isinstance(test, ast.Constant) and isinstance(test.value, bool)
        )
        if not weak:
            strong_assertions += 1

    raises = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
        and node.func.attr == "raises"
    )
    return {
        "assertions": len(assertions),
        "strong_assertions": strong_assertions,
        "raises": raises,
    }


def _oracle_fingerprints(tree: ast.AST) -> list[str]:
    """Fingerprint complete assertions so expected values cannot be rewritten."""
    fingerprints = [
        ast.dump(node.test, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]
    fingerprints.extend(
        "raises:"
        + ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
        and node.func.attr == "raises"
    )
    return sorted(fingerprints)
