"""Harness v2, writing skeletons: the ``headings`` check, ledger_sum modes
(value / band / clock), ``fallback_element`` on any check (a check that cannot
find its structure hands the call to a judge element), and the rule-1 lint
allowing adult family relations in an all-adult cast (FR-A18)."""
from __future__ import annotations

import pytest

from crucibleforge import judge, lint, report, session_checks as sc

PIECE = """## Setup
Short sentence here. Another one.

## Turn
The ledger: rope 3, lamp oil 2, bread four. Total: 9 coins.
Left the dock at 7:40, reached the reef at 8:15, back by 9:05. Out 85 minutes.

## Close
Done.
"""


def _run(*checks, text=PIECE):
    return sc.run_checks({"checks": list(checks)}, [text])


def test_headings_present_order_and_credit():
    r = _run({"id": "h", "type": "headings", "headings": ["Setup", "Turn", "Close"],
              "in_order": True})["results"][0]
    assert r["pass"]
    r = _run({"id": "h", "type": "headings", "headings": ["Setup", "Coda", "Close"]})["results"][0]
    assert not r["pass"] and r["credit"] == pytest.approx(2 / 3, abs=1e-3)
    r = _run({"id": "h", "type": "headings", "headings": ["Close", "Setup"], "in_order": True}
             )["results"][0]
    assert not r["pass"] and r["credit"] == 0.5
    r = _run({"id": "h", "type": "headings", "headings": [{"section": "Turn"}], "bold": True},
             text="**Turn**\nbody")["results"][0]
    assert r["pass"]


@pytest.mark.parametrize("chk,ok", [
    ({"mode": "value", "item": r"(?:rope|oil|bread) (\w+)", "total": r"Total: (\d+)"}, True),
    ({"mode": "value", "item": r"(?:rope|oil|bread) (\w+)", "total": r"Total: (\d+)",
      "tolerance": 0}, True),
    ({"mode": "band", "item": r"(?:rope|oil|bread) (\w+)", "band": [8, 10]}, True),
    ({"mode": "band", "item": r"(?:rope|oil|bread) (\w+)", "band": [1, 5]}, False),
    ({"mode": "clock", "item": r"(?:at|by) (\d{1,2}:\d{2})", "total": r"Out (\d+) minutes"}, True),
    ({"mode": "clock", "item": r"(?:at|by) (\d{1,2}:\d{2})", "band": [90, 120]}, False),
])
def test_ledger_modes(chk, ok):
    r = _run(dict(chk, id="l", type="ledger_sum"))["results"][0]
    assert r["pass"] is ok, r


def test_clock_running_backwards_fails():
    r = _run({"id": "l", "type": "ledger_sum", "mode": "clock",
              "item": r"at (\d{1,2}:\d{2})", "band": [0, 999]},
             text="at 9:00 then at 8:30")["results"][0]
    assert not r["pass"] and "backwards" in r["detail"]


def test_fallback_element_replaces_a_check_that_found_nothing():
    chk = {"id": "l", "type": "ledger_sum", "item": r"cost (\d+)", "total": r"sum (\d+)",
           "fallback_element": "E-ledger"}
    out = _run(chk, {"id": "h", "type": "headings", "headings": ["Setup"]})
    led = out["results"][0]
    assert led["fallback"] and led["fallback_element"] == "E-ledger"
    assert out["rate"] == 1.0                      # only the headings check scores
    # the judge is asked the fallback element only now
    els = [{"id": "E-ledger", "q": "Do the ledger numbers add up?"}, {"id": "E2", "q": "q"}]
    row = {"checks": out}
    ask, auto = judge.confirm_split(row, els)
    assert [e["id"] for e in ask] == ["E-ledger", "E2"] and auto == []
    # a ledger that WAS found scores itself; the element is not asked or scored
    found = _run(dict(chk, item=r"(?:rope|oil|bread) (\w+)", total=r"Total: (\d+)"))
    assert not found["results"][0]["fallback"] and found["rate"] == 1.0
    ask, auto = judge.confirm_split({"checks": found}, els)
    assert [e["id"] for e in ask] == ["E2"] and auto == []
    # a fallen-back check adds nothing to C
    r = {"checks": out, "judge": {"elements": {"results": [{"id": "E-ledger", "pass": True,
                                                              "credit": 1.0}]}}}
    got = report.wc_credit(r)
    assert got["C"] == 1.0 and got["n_checks"] == 1


def test_sentence_cap_with_no_section_found_can_fall_back():
    out = _run({"id": "s", "type": "sentence_cap", "sections": [{"section": "Nope", "cap": 5}],
                "fallback_element": "E-s"})
    assert out["results"][0]["fallback"]


def test_v2_lint_checks_fallback_and_mode():
    case = {"judge_elements": [{"id": "E1"}],
            "checks": [{"id": "a", "type": "ledger_sum", "mode": "tally", "fallback_element": "E9",
                        "item": "x", "total": "y"}]}
    ps = lint.v2_case_problems(case, {"level": 2})
    assert any("fallback_element 'E9'" in p for p in ps)
    assert any("value, band or clock" in p for p in ps)


TURNS = ["Bought rope 3 and oil 2.", "Then bread 4.", "Then nails 1."]


def _turns(chk, replies=TURNS):
    return sc.run_checks({"checks": [dict(chk, id="l", type="ledger_sum")]}, replies)["results"][0]


def test_ledger_expected_running_tally_per_turn():
    item = r"(?:rope|oil|bread|nails) (\d+)"
    assert _turns({"item": item, "expected": [[1, 5], [2, 9], [3, 10]]})["pass"]
    r = _turns({"item": item, "expected": [[1, 5], [2, 8], [3, 10]]})
    assert not r["pass"] and r["credit"] == pytest.approx(2 / 3, abs=1e-3)
    assert "t2" in r["detail"]
    r = _turns({"item": item, "expected": [[2, 8]], "tolerance": 1})
    assert r["pass"]
    # with a total too, the overall rule is one more checkpoint
    r = _turns({"item": item, "total": r"Total (\d+)", "expected": [[1, 5]]},
               TURNS[:2] + ["Then nails 1. Total 11"])
    assert not r["pass"] and r["credit"] == 0.5


def test_ledger_expected_clock_per_turn():
    replies = ["We leave at 7:40.", "By 8:15 the reef.", "Home at 9:05."]
    chk = {"mode": "clock", "item": r"(?:at|By) (\d{1,2}:\d{2})"}
    assert _turns(dict(chk, expected=[[1, "7:40"], [2, "8:15"], [3, "9:05"]]), replies)["pass"]
    r = _turns(dict(chk, expected=[[2, "8:20"], [3, 545]]), replies)       # 545 min = 9:05
    assert not r["pass"] and r["credit"] == 0.5


def _el(eid, **kw):
    return dict({"id": eid, "q": "q"}, **kw)


def test_every_window_element_joins_every_call_within_the_cap():
    els = [_el(f"S{i}", section="A") for i in range(9)] + [_el("T1", turns=[1])] \
        + [_el("NEG", negative=True, window="all")]
    groups = judge.window_groups(els)
    assert all(any(e["id"] == "NEG" for e in g) for _, g in groups)
    assert all(len(g) <= judge.MAX_GROUP_ELEMENTS for _, g in groups)
    assert sum(e["id"] != "NEG" for _, g in groups for e in g) == 10
    only = judge.window_groups([_el("NEG", negative=True, every_window=True)])
    assert only == [(("t", ()), [only[0][1][0]])]


@pytest.mark.parametrize("passes,scale,want_pass,want_credit", [
    ([False, True, False], 1, True, 0.0),        # a disclaimer in one window = drift, once
    ([False, False, False], 1, False, 1.0),
])
def test_worst_window_negative_counts_once(passes, scale, want_pass, want_credit):
    # ``passes`` = found per window; a negative result passes on NO
    e = _el("NEG", negative=True, window="all")
    rs = [{"id": "NEG", "found": f, "pass": not f} for f in passes]
    r = judge.worst_window(e, rs)
    assert r["found"] is want_pass and r["pass"] is (not want_pass)
    assert r["credit"] == want_credit
    assert r["pass_windows"] == f"{3 - sum(passes)}/3"


def test_worst_window_scale2_and_positive():
    neg = _el("N2", negative=True, scale=2, window="all")
    r = judge.worst_window(neg, [{"id": "N2", "pass": True, "score": s} for s in (0, 2, 1)])
    assert r["score"] == 2
    pos = _el("P2", scale=2, window="all")
    scores = [{"id": "P2", "pass": True, "score": s} for s in (2, 1, 2)]
    r = judge.worst_window({**pos, "aggregate": "min"}, scores)     # explicit floor
    assert r["score"] == 1
    r = judge.worst_window(pos, scores)       # H2 2026-11-08: default is the mean over windows
    assert abs(r["credit"] - 5 / 6) < 1e-6 and r["pass"]
    pos1 = _el("P1", window="all")
    r = judge.worst_window(pos1, [{"id": "P1", "pass": True}, {"id": "P1", "pass": False}])
    assert r["pass"] is False


def test_collapse_keeps_one_result_per_every_window_element():
    els = [_el("A", section="x"), _el("NEG", negative=True, window="all")]
    res = [{"id": "A", "pass": True}, {"id": "NEG", "pass": True, "found": False},
           {"id": "NEG", "pass": False, "found": True}, {"id": "NEG", "pass": True, "found": False}]
    out = judge._collapse_every_window(els, res)
    assert [r["id"] for r in out] == ["A", "NEG"]
    assert out[1]["found"] is True and out[1]["pass"] is False


ADULTS = {"id": "FR-A18", "category": "nsfw", "rubric": "erp_session",
          "system": "Mara, 52, and her adult child Theo, 29; his sister Ines, 31.",
          "cast": [{"name": "Mara", "age": 52}, {"name": "Theo", "age": 29},
                   {"name": "Ines", "age": 31}]}


@pytest.mark.parametrize("text", [
    "Mara visits her adult child at the harbour.",
    "Theo, the eldest child of the family, is 29.",
    "Their grown children all left town.",
])
def test_rule1_allows_adult_family_relations_in_an_all_adult_cast(text):
    case = dict(ADULTS, prompt=text)
    assert not [h for h in lint.content_hits(case, None, []) if h["rule"] == 1]


def test_rule1_still_fires_without_adult_statements_or_out_of_relation():
    case = dict(ADULTS, prompt="Mara visits her adult child.")
    case["cast"] = [{"name": "Mara", "age": 52}, {"name": "Theo"}]        # Theo has no age
    assert [h for h in lint.content_hits(case, None, []) if h["rule"] == 1]
    case = dict(ADULTS, prompt="A child wanders into the scene.")       # not a relation
    assert [h for h in lint.content_hits(case, None, []) if h["rule"] == 1]
    case = dict(ADULTS, prompt="She is sixteen years old.")
    assert [h for h in lint.content_hits(case, None, []) if h["rule"] == 1]
    case = dict(ADULTS, prompt="her teen daughter")                       # not a family word
    assert [h for h in lint.content_hits(case, None, []) if h["rule"] == 1]
    unstated = dict(ADULTS, system="Mara and her adult child Theo.", prompt="")
    assert not lint.adult_cast(unstated)                                  # ages not in the card
