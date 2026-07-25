def add(left: float, right: float) -> float:
    return left + right


def divide(left: float, right: float) -> float:
    if right == 0:
        raise ValueError("right must not be zero")
    return left / right
