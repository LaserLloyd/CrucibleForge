import pytest

from tabsplit.split import split_by_weights


def test_simple_proportion():
    assert split_by_weights(1000, {"ana": 2, "ben": 1, "cy": 1}) == {"ana": 500, "ben": 250, "cy": 250}


def test_largest_remainder_exact_integer_arithmetic():
    # remainders that floats order wrongly
    assert split_by_weights(80034, {"a": 7, "b": 6, "c": 1, "d": 1}) == {"a": 37349, "b": 32014, "c": 5336, "d": 5335}
    assert split_by_weights(57036, {"a": 6, "b": 3, "c": 1}) == {"a": 34222, "b": 17111, "c": 5703}


def test_ties_follow_weights_order():
    assert split_by_weights(100, {"cy": 1, "ana": 1, "ben": 1}) == {"cy": 34, "ana": 33, "ben": 33}
    assert split_by_weights(2, {"z": 1, "y": 1, "x": 1}) == {"z": 1, "y": 1, "x": 0}


def test_sums_always():
    import random
    rnd = random.Random(7)
    for _ in range(300):
        ws = {f"m{i}": rnd.randint(1, 9) for i in range(rnd.randint(1, 6))}
        total = rnd.randint(0, 10**7)
        s = split_by_weights(total, ws)
        assert sum(s.values()) == total and list(s) == list(ws)


@pytest.mark.parametrize("ws", [{}, {"a": 0, "b": 1}, {"a": -1, "b": 2}, {"a": 1.5}, {"a": True}])
def test_bad_weights(ws):
    with pytest.raises(ValueError):
        split_by_weights(100, ws)
