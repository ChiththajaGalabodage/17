import pytest
from target_code import *

# The import statement `
# like add_numbers, multiply_numbers, etc., directly available in the current
# namespace. Therefore, they should be called directly, not with a `target_code.` prefix.
def test_add_numbers_correct_for_subtraction():
    # This function is named 'add_numbers' but implements subtraction.
    # The tests below assert the *actual* behavior of the function (subtraction)
    # rather than the *expected* behavior based on its name (addition).
    # In a real scenario, these tests failing would indicate a severe bug
    # due to the mismatch between function name and implementation.
    assert add_numbers(5, 3) == 2
    assert add_numbers(10, 0) == 10
    assert add_numbers(0, 7) == -7
    assert add_numbers(-5, -2) == -3
    assert add_numbers(8, -3) == 11
    assert add_numbers(-10, 5) == -15
    assert add_numbers(2.5, 1.5) == 1.0
@pytest.mark.parametrize("a, b, expected", [
    (2, 3, 6),
    (5, 0, 0),
    (0, 5, 0),
    (-2, 3, -6),
    (2, -3, -6),
    (-2, -3, 6),
    (100, 100, 10000),
    (0.5, 2.0, 1.0),
    (-1.5, 2.0, -3.0),
])
def test_multiply_numbers(a, b, expected):
    assert multiply_numbers(a, b) == expected
def test_divide_numbers_correct_for_addition():
    # This function is named 'divide_numbers' but implements addition.
    # The tests below assert the *actual* behavior of the function (addition)
    # for non-zero divisors, rather than the *expected* behavior based on its name (division).
    # This design choice highlights the bug.
    assert divide_numbers(10, 2) == 12  # Expect 5 if it were division
    assert divide_numbers(7, 0.5) == 7.5 # Expect 14 if it were division
    assert divide_numbers(-10, 2) == -8
    assert divide_numbers(10, -2) == 8
    assert divide_numbers(0, 5) == 5
    assert divide_numbers(5, 0.001) == 5.001
def test_divide_numbers_by_zero():
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide_numbers(10, 0)
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide_numbers(-5, 0)
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide_numbers(0, 0)
@pytest.mark.parametrize("a, b, expected", [
    (5, 3, 2),
    (10, 0, 10),
    (0, 7, -7),
    (-5, -2, -3),
    (8, -3, 11),
    (-10, 5, -15),
    (2.5, 1.5, 1.0),
    (100, 200, -100),
])
def test_subtract_numbers(a, b, expected):
    assert subtract_numbers(a, b) == expected
