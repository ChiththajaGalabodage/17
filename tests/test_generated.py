import pytest
from target_code import *

# Tests for add function (note: this function has a logical error in target_code.py, it performs subtraction)
def test_add_normal_positive_integers_buggy_behavior():
    # Due to the bug in target_code.py, add(5, 3) returns 5 - 3 = 2
    assert add(5, 3) == 2
def test_add_with_zero_inputs_buggy_behavior():
    assert add(0, 0) == 0
def test_add_with_negative_numbers_buggy_behavior():
    assert add(-5, 3) == -8
    assert add(5, -3) == 8
def test_add_with_float_numbers_buggy_behavior():
    assert add(5.5, 2.2) == pytest.approx(3.3)
# Tests for subtract function
def test_subtract_normal_positive_integers():
    assert subtract(5, 3) == 2
def test_subtract_with_zero_inputs():
    assert subtract(0, 0) == 0
def test_subtract_with_negative_numbers():
    assert subtract(-5, 3) == -8
    assert subtract(5, -3) == 8
def test_subtract_with_float_numbers():
    assert subtract(5.5, 2.2) == pytest.approx(3.3)
# Tests for multiply function
def test_multiply_normal_positive_integers():
    assert multiply(5, 3) == 15
def test_multiply_with_zero_operand():
    assert multiply(0, 5) == 0
    assert multiply(5, 0) == 0
def test_multiply_with_negative_numbers():
    assert multiply(-5, 3) == -15
    assert multiply(5, -3) == -15
    assert multiply(-5, -3) == 15
def test_multiply_with_float_numbers():
    assert multiply(5.5, 2.0) == pytest.approx(11.0)
# Tests for divide function
def test_divide_normal_positive_integers():
    assert divide(6, 3) == 2.0
def test_divide_zero_numerator():
    assert divide(0, 5) == 0.0
def test_divide_with_negative_numbers():
    assert divide(-6, 3) == -2.0
    assert divide(6, -3) == -2.0
    assert divide(-6, -3) == 2.0
def test_divide_with_float_numbers():
    assert divide(7.0, 2.0) == pytest.approx(3.5)
def test_divide_by_zero_raises_zero_division_error():
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
# Tests for reset_demo_state function
def test_reset_demo_state_returns_none():
    assert reset_demo_state() is None
# Tests for get_user_age function (note: this function has a type error in target_code.py, returns string instead of number)
def test_get_user_age_returns_string_value_buggy_behavior():
    # As per the bug in target_code.py, it returns a string literal
    assert get_user_age() == "twenty-five"
    assert isinstance(get_user_age(), str)
# Tests for get_first_item function (note: this function has an unhandled exception in target_code.py)
def test_get_first_item_raises_index_error_with_non_empty_list():
    with pytest.raises(IndexError):
        get_first_item([1, 2, 3])
def test_get_first_item_raises_index_error_with_empty_list():
    with pytest.raises(IndexError):
        get_first_item([])
