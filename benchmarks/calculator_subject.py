"""Small clean subject used to smoke-test the research experiment pipeline.

This benchmark is intentionally simple and is not sufficient evidence for the
thesis by itself. It gives the harness a versioned, passing reference subject
before real open-source projects are added to the experiment manifest.
"""


def add(left: float, right: float) -> float:
    return left + right


def subtract(left: float, right: float) -> float:
    return left - right


def multiply(left: float, right: float) -> float:
    return left * right


def divide(left: float, right: float) -> float | str:
    if right == 0:
        return "Error: Cannot divide by zero!"
    return left / right


def reset_demo_state() -> None:
    return None


def get_user_age() -> int:
    return 25


def get_first_item(values: list[object]) -> object:
    if not values:
        raise ValueError("values must not be empty")
    return values[0]
