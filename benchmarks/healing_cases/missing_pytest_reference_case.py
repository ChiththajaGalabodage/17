import pytest

from benchmarks.healing_cases.subject import add


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(1, 2, 3), (-1, 1, 0)],
)
def test_add_contract(left, right, expected):
    assert add(left, right) == expected
