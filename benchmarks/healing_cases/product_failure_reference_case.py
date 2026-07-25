from benchmarks.healing_cases.subject import add


def test_add_contract_failure_is_preserved():
    assert add(1, 2) == 3
