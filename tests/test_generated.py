import pytest
from target_code import *

def test_add_numbers_basic():
    result = add_numbers(1, 2)
    assert result is not None
