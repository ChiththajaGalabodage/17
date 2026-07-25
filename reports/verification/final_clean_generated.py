import pytest
from benchmarks.calculator_subject import *
from datetime import datetime

def test_add_returns_the_sum_of_two_numbers():
    assert add(1, 2) == 3
    assert add(-4, 9) == 5
def test_subtract_returns_the_difference_of_two_numbers():
    assert subtract(7, 2) == 5
    assert subtract(3, 8) == -5
def test_multiply_returns_the_product_of_two_numbers():
    assert multiply(3, 4) == 12
    assert multiply(-2, 5) == -10
def test_divide_returns_quotient_and_handles_zero():
    assert divide(8, 2) == 4
    assert divide(9, 3) == 3
    assert divide(5, 0) == 'Error: Cannot divide by zero!'
def test_reset_demo_state_is_repeatable():
    assert reset_demo_state() is None
    assert reset_demo_state() is None
def test_get_user_age_returns_a_realistic_integer():
    result = get_user_age()
    assert isinstance(result, int)
    assert 0 <= result <= 130
def test_get_first_item_returns_the_first_value():
    assert get_first_item([10, 20, 30]) == 10
    assert get_first_item(['a', 'b']) == 'a'
