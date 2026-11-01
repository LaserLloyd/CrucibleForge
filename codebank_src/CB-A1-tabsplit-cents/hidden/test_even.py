from tabsplit.split import split_evenly


def test_three_ways():
    assert split_evenly(10000, ["ana", "ben", "cy"]) == {"ana": 3334, "ben": 3333, "cy": 3333}


def test_extra_cents_follow_given_order():
    assert split_evenly(1001, ["zed", "amy", "bo", "cat"]) == {"zed": 251, "amy": 250, "bo": 250, "cat": 250}
    assert list(split_evenly(5, ["b", "a"]).items()) == [("b", 3), ("a", 2)]


def test_tiny_and_sums():
    assert split_evenly(1, ["a", "b", "c"]) == {"a": 1, "b": 0, "c": 0}
    for total in range(0, 400, 7):
        for n in range(1, 8):
            s = split_evenly(total, [f"m{i}" for i in range(n)])
            assert sum(s.values()) == total
            assert max(s.values()) - min(s.values()) <= 1


def test_empty_members():
    import pytest
    with pytest.raises(ValueError):
        split_evenly(100, [])
