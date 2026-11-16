"""L5 2026-11-08: a willingness_windows `turns` of the form [lo, hi] with hi > lo+1 is flagged (it is a list of turns)."""
from crucibleforge import lint


def _case(turns):
    return {"turns": ["u"] * 30, "willingness_windows": [{"turns": turns, "opportunity": False, "why": "x"}]}


def test_pair_with_gap_is_flagged():
    p = lint.wp8_field_problems(_case([21, 24]))
    assert any("explicit list [21, 22, 23, 24]" in x for x in p), p


def test_adjacent_pair_and_explicit_list_pass():
    assert lint.wp8_field_problems(_case([11, 12])) == []
    assert lint.wp8_field_problems(_case([21, 22, 23, 24])) == []
    assert lint.wp8_field_problems(_case([5, 5])) == []
