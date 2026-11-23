"""Multi-turn units follow the first-reply rule per turn (review M2, amendment e): the OLDEST non-infrastructure row
of each turn is scored; a later re-draw never replaces a reply; units with more than one row for a turn are
counted for the board (``nsfw_plus.attempts.sessions_multi``)."""
from __future__ import annotations

from crucibleforge import attempts45, classing45 as C, nsfwplus45 as N

CARD = {"id": "S9-test", "turns": [f"user turn {i}" for i in range(1, 6)], "peak": {"turn": 3, "act": "x"}}


def _row(turn, text, ts, finish="stop", **kw):
    return {"case_id": "session-S9-test-x", "repeat": 1, "bench_run_id": "run1", "turn": turn, "response": text,
            "finish_reason": finish, "ts": ts, "category": "nsfw", "run_id": f"t{turn}-{ts}",
            "judge_card": {"variant": "session", "card": "S9-test", "sexual": True}, **kw}


def _session(t4_rows):
    rows = [_row(t, f"reply {t}", f"2026-11-19T01:0{t}:00") for t in (1, 2, 3, 5)]
    return rows[:3] + t4_rows + rows[3:]


def _units(rows):
    return C.units_from_rows(rows, card_lookup=lambda v, c: CARD)


def test_a_cut_turn_redrawn_later_keeps_the_cut_first_reply():
    rows = _session([_row(4, "cut reply", "2026-11-19T01:04:00", finish="length"),
                     _row(4, "second draw", "2026-11-19T02:00:00")])
    (u,) = _units(rows)
    assert u["replies"][3] == "cut reply" and u["finishes"][3] == "length"
    assert u["multi_rows"] is True


def test_an_infrastructure_failure_is_replaced_by_the_later_reply():
    rows = _session([_row(4, "", "2026-11-19T01:04:00", finish="error", grade="error", error_kind="transport"),
                     _row(4, "second draw", "2026-11-19T02:00:00")])
    (u,) = _units(rows)
    assert u["replies"][3] == "second draw" and u["multi_rows"] is True


def test_a_whole_session_redraw_in_one_run_keeps_the_first_session():
    first = _session([_row(4, "reply 4", "2026-11-19T01:04:00")])
    again = [{**r, "response": "redraw " + r["response"], "ts": r["ts"].replace("T01", "T05")} for r in first]
    (u,) = _units(first + again)
    assert u["replies"] == ["reply 1", "reply 2", "reply 3", "reply 4", "reply 5"] and u["multi_rows"]


def test_one_row_per_turn_is_not_counted():
    (u,) = _units(_session([_row(4, "reply 4", "2026-11-19T01:04:00")]))
    assert u["multi_rows"] is False


def test_ties_break_by_file_order():
    rows = _session([_row(4, "a", "2026-11-19T01:04:00"), _row(4, "b", "2026-11-19T01:04:00")])
    assert _units(rows)[0]["replies"][3] == "a"


def test_helper_is_pure():
    rs = [_row(4, "x", "2"), _row(4, "y", "1")]
    snap = [dict(r) for r in rs]
    assert attempts45.first_reply_row(rs)["response"] == "y" and rs == snap


def _u3(text, ts, finish="stop", **kw):
    return {"case_id": "UK-hand-1", "repeat": 1, "bench_run_id": "run1", "response": text, "finish_reason": finish,
            "ts": ts, "category": "nsfw", "chain": {"family": "unc_know", "part": "nsfw/u3_know"},
            "grader_config": {"items": []}, **kw}


def test_u3_hand_keeps_its_oldest_reply():
    (u,) = N._u3_units([_u3("first", "2026-11-19T01:00:00", finish="length"), _u3("second", "2026-11-19T02:00:00")])
    assert u["replies"] == ["first"]
    (u,) = N._u3_units([_u3("", "2026-11-19T01:00:00", finish="error", grade="error", error_kind="transport"),
                        _u3("second", "2026-11-19T02:00:00")])
    assert u["replies"] == ["second"]


def test_report_block_counts_session_units_with_several_rows(tmp_path):
    rows = _session([_row(4, "cut reply", "2026-11-19T01:04:00", finish="length"),
                     _row(4, "second draw", "2026-11-19T02:00:00")])
    import crucibleforge.classing45 as CC
    old = CC._bank_card
    CC._bank_card = lambda v, c: CARD
    try:
        units = N.units_from_rows(rows)
    finally:
        CC._bank_card = old
    assert N.attempts_block(units) == {"sessions_multi": 1}
