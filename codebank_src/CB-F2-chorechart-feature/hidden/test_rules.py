import pytest

from chorechart.rotation import assign

M = ["Ana", "Ben", "Cy", "Dee"]


def test_collision_passes_to_next_free():
    # week 0: dishes->Ana; bins: Ben away -> Cy; laundry: Cy taken -> Dee
    assert assign(["dishes", "bins", "laundry"], M, 0, away={"Ben"}) == {"dishes": "Ana", "bins": "Cy", "laundry": "Dee"}


def test_more_chores_than_present_members():
    got = assign(["a", "b", "c", "d", "e"], M, 2, away={"Ana", "Dee"})
    assert got == {"a": "Cy", "b": "Ben", "c": "Ben", "d": "Ben", "e": "Cy"}


def test_wraps_and_negative_weeks():
    assert assign(["x"], M, -1) == {"x": "Dee"}
    assert assign(["x", "y"], M, 7) == {"x": "Dee", "y": "Ana"}
    assert assign(["x"], M, 10**9 + 2) == assign(["x"], M, 2)


def test_everyone_away():
    assert assign(["x", "y"], M, 0, away=set(M)) == {"x": None, "y": None}


@pytest.mark.parametrize("chores,members", [([], M), (["x"], []), (["x", "x"], M), (["x"], ["Ana", "Ana"])])
def test_bad_input(chores, members):
    with pytest.raises(ValueError):
        assign(chores, members, 0)


def test_result_order_is_chore_order():
    assert list(assign(["z", "a", "m"], M, 3)) == ["z", "a", "m"]
