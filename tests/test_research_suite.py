"""Research benchmark suite for continuous testing experiments.

This suite intentionally contains both passing and failing tests so the
comparison report reflects actual pytest outcomes rather than a forced
100% pass rate.
"""
from __future__ import annotations

import target_code as tc


PASS_CASES: list[tuple[str, object, object, object]] = []
for value in range(1, 51):
    PASS_CASES.append(("subtract", value + 100, value, (value + 100) - value))
for value in range(1, 51):
    PASS_CASES.append(("multiply", value, 2, value * 2))
for _ in range(10):
    PASS_CASES.append(("reset_demo_state", None, None, None))

FAIL_CASES: list[tuple[str, object, object, object]] = []
for value in range(1, 26):
    FAIL_CASES.append(("add", value, value + 2, value + value + 2))
for value in range(1, 6):
    FAIL_CASES.append(("get_user_age", None, None, value))
for value in range(1, 6):
    FAIL_CASES.append(("get_first_item", [value, value + 1, value + 2], None, value + 1))
for _ in range(5):
    FAIL_CASES.append(("divide", 5, 0, "Error"))


def _register_test(name: str, factory) -> None:
    test_fn = factory()
    test_fn.__name__ = name
    test_fn.__qualname__ = name
    globals()[name] = test_fn


def _make_pass_test(op_name: str, left, right, expected):
    def test_case() -> None:
        if op_name == "reset_demo_state":
            assert tc.reset_demo_state() is None
            return

        operation = getattr(tc, op_name)
        assert operation(left, right) == expected

    return test_case


def _make_fail_test(op_name: str, left, right, expected):
    def test_case() -> None:
        if op_name == "get_user_age":
            assert isinstance(tc.get_user_age(), int)
            return

        if op_name == "get_first_item":
            assert tc.get_first_item(left) == expected
            return

        operation = getattr(tc, op_name)
        assert operation(left, right) == expected

    return test_case


for index, case in enumerate(PASS_CASES, start=1):
    globals()[f"test_pass_{index:03d}"] = _make_pass_test(*case)

for index, case in enumerate(FAIL_CASES, start=1):
    globals()[f"test_fail_{index:03d}"] = _make_fail_test(*case)
