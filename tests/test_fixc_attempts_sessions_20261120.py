"""Fix pass C (decision f): a session is ONE unit. Its rows split into attempts where the turn numbering restarts; a
continuation after an infrastructure failure at the same turn is joined; the first attempt with no infrastructure
failure scores; two conversations are never mixed. Same selection for Chat sessions, NSFW+ sessions and U3 hands."""
from __future__ import annotations

from crucibleforge import attempts45 as A
from crucibleforge import classing45 as C
from crucibleforge import nsfwplus45 as N
from crucibleforge import report

CARD = {"id": "S9-test", "turns": [f"user turn {i}" for i in range(1, 6)], "peak": {"turn": 3, "act": "x"}}


def _row(turn, text, ts, finish="stop", run="run1", **kw):
    return {"case_id": "session-S9-test-x", "repeat": 1, "bench_run_id": run, "turn": turn, "response": text,
            "finish_reason": finish, "ts": ts, "category": "nsfw", "run_id": f"t{turn}-{ts}",
            "judge_card": {"variant": "session", "card": "S9-test", "sexual": True}, **kw}


def _err(turn, ts):
    return _row(turn, "", ts, finish="error", grade="error", error_kind="transport")


def _units(rows):
    return C.units_from_rows(rows, card_lookup=lambda v, c: CARD)


def test_an_aborted_draw_and_a_full_redraw_are_never_spliced():
    # the verifier's input: draw 1 errors at T3, draw 2 is a whole new session
    d1 = [_row(1, "A1", "2026-11-19T01:01:00"), _row(2, "A2", "2026-11-19T01:02:00"), _err(3, "2026-11-19T01:03:00")]
    d2 = [_row(t, f"B{t}", f"2026-11-19T02:0{t}:00") for t in range(1, 6)]
    (u,) = _units(d1 + d2)
    assert u["replies"] == ["B1", "B2", "B3", "B4", "B5"] and u["multi_rows"] is True


def test_the_runners_session_error_row_is_an_attempt_of_its_own():
    err = {**_err(None, "2026-11-19T01:00:00"), "turn": None}
    d2 = [_row(t, f"B{t}", f"2026-11-19T02:0{t}:00") for t in range(1, 6)]
    (u,) = _units([err] + d2)
    assert u["replies"] == ["B1", "B2", "B3", "B4", "B5"]


def test_a_continuation_after_an_infrastructure_failure_at_that_turn_is_joined():
    rows = [_row(1, "A1", "2026-11-19T01:01:00"), _row(2, "A2", "2026-11-19T01:02:00"), _err(3, "2026-11-19T01:03:00"),
            _row(3, "A3", "2026-11-19T01:10:00"), _row(4, "A4", "2026-11-19T01:11:00"),
            _row(5, "A5", "2026-11-19T01:12:00")]
    (u,) = _units(rows)
    assert u["replies"] == ["A1", "A2", "A3", "A4", "A5"]


def test_a_second_complete_draw_is_ignored_and_counted():
    d1 = [_row(t, f"A{t}", f"2026-11-19T01:0{t}:00", finish="length" if t == 4 else "stop") for t in range(1, 6)]
    d2 = [_row(t, f"B{t}", f"2026-11-19T02:0{t}:00") for t in range(1, 6)]
    (u,) = _units(d1 + d2)
    assert u["replies"] == [f"A{t}" for t in range(1, 6)] and u["finishes"][3] == "length"
    assert N.attempts_block([{**u, "numbered": True, "multi_rows": u["multi_rows"]}]) == {"sessions_multi": 1}


def test_chat_sessions_take_the_same_selection_on_the_board():
    d1 = [{**_row(t, f"A{t}", f"2026-11-19T01:0{t}:00"), "category": "rpdeep", "profile": "bench45",
           "case_id": "RD1-marisol"} for t in (1, 2)] + \
         [{**_err(3, "2026-11-19T01:03:00"), "category": "rpdeep", "profile": "bench45", "case_id": "RD1-marisol"}]
    d2 = [{**_row(t, f"B{t}", f"2026-11-19T02:0{t}:00"), "category": "rpdeep", "profile": "bench45",
           "case_id": "RD1-marisol"} for t in range(1, 6)]
    kept = [r for r in A.select(d1 + d2) if r["case_id"] == "RD1-marisol"]
    assert [r["response"] for r in kept] == ["B1", "B2", "B3", "B4", "B5"]
    assert kept[0]["attempts45"]["replaced_infra"] == 1


def test_u3_hands_take_the_same_selection():
    def u3(text, ts, finish="stop", **kw):
        return {"case_id": "UK-hand-1", "repeat": 1, "bench_run_id": "run1", "response": text, "finish_reason": finish,
                "ts": ts, "run_id": ts, "category": "nsfw", "chain": {"family": "unc_know", "part": "nsfw/u3_know"},
                "grader_config": {"items": []}, **kw}
    (u,) = N._u3_units([u3("", "2026-11-19T01:00:00", finish="error", grade="error", error_kind="transport"),
                        u3("second", "2026-11-19T02:00:00"), u3("third", "2026-11-19T03:00:00")])
    assert u["replies"] == ["second"]
