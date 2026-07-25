import pytest

from benchmarks import calculator_subject as calculator


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(1, 2, 3), (-4, 9, 5), (0, 0, 0)],
)
def test_add(left, right, expected):
    assert calculator.add(left, right) == expected


def test_subtract():
    assert calculator.subtract(7, 2) == 5
    assert calculator.subtract(3, 8) == -5


def test_multiply():
    assert calculator.multiply(3, 4) == 12
    assert calculator.multiply(-2, 5) == -10


def test_divide():
    assert calculator.divide(8, 2) == 4
    assert calculator.divide(5, 0) == "Error: Cannot divide by zero!"


def test_reset_demo_state():
    assert calculator.reset_demo_state() is None


def test_get_user_age():
    age = calculator.get_user_age()
    assert isinstance(age, int)
    assert 0 <= age <= 130


def test_get_first_item():
    assert calculator.get_first_item([10, 20, 30]) == 10
    with pytest.raises(ValueError):
        calculator.get_first_item([])
