from tabsplit.split import split_by_weights, split_evenly


def test_even_refund_mirrors():
    assert split_evenly(-10000, ["ana", "ben", "cy"]) == {"ana": -3334, "ben": -3333, "cy": -3333}
    assert split_evenly(-1, ["a", "b"]) == {"a": -1, "b": 0}


def test_weighted_refund_mirrors():
    assert split_by_weights(-100, {"cy": 1, "ana": 1, "ben": 1}) == {"cy": -34, "ana": -33, "ben": -33}
    assert split_by_weights(-80034, {"a": 7, "b": 6, "c": 1, "d": 1}) == {"a": -37349, "b": -32014, "c": -5336, "d": -5335}


def test_refund_sums():
    for total in range(-300, 0, 11):
        assert sum(split_evenly(total, ["a", "b", "c", "d"]).values()) == total
        assert sum(split_by_weights(total, {"a": 3, "b": 2, "c": 2}).values()) == total
