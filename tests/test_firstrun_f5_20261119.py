"""F5 of the first fresh reference run (2026-11-19): profile key ``cut_at_full_budget`` (omit | zero).

``omit`` (default, D10): a reply cut at its token ceiling leaves the mean and is counted; a part with more than a
quarter cut AND at least two cut prints "not measured". ``zero``, on the short-keyed-answer parts only (Trace,
Predict, State, Tally, Know): a reply with finish reason length whose output tokens are at least 98% of the full
ceiling "did not finish": it scores 0, stays in the mean, and the part says "n of m did not finish within
<ceiling> tokens". A cut below the full ceiling (a provider cap) stays under ``omit``. Toy board."""
from __future__ import annotations

import pytest

from crucibleforge import chains, report

from _lane_r_toy import PASS, board, cut, flat, prof45, row, toys  # noqa: F401  (toys: autouse fixture)

CEIL = 1000


def _prof(mode):
    prof = prof45()
    prof["chains"]["work/a"] = flat("xa", n=4, ceiling=CEIL)
    if mode is not None:
        prof["cut_at_full_budget"] = mode
    return prof


def _policy(n_full, n_below=0):
    """The first ``n_full`` units of A are cut at the full ceiling, the next ``n_below`` below it (a provider cap)."""
    def pol(lv, cid):
        if cid.startswith("FLAT-xa-"):
            k = int(cid.split("-")[2])
            if k <= n_full:
                return cut("PAS", n=CEIL)
            if k <= n_full + n_below:
                return cut("PAS", n=CEIL // 2)
        return "PASS"
    return pol


@pytest.fixture
def short_a(monkeypatch):
    monkeypatch.setattr(chains, "SHORT_KEYED_PARTS", frozenset({"work/a"}))


def test_omit_is_the_default_and_leaves_cut_units_out(tmp_path, monkeypatch, short_a):
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": _policy(1)}, prof=_prof(None))
    det = stats["m"]["scorecard"]["ladder"]["work"]["a"]
    assert det["cut"] == {"cut": 1, "of": 4, "empty": 0} and not det.get("did_not_finish")
    assert stats["m"]["scorecard"]["parts"]["work"]["a"] == pytest.approx(5.0)       # the three answered, all right


def test_zero_grades_a_full_budget_cut_as_delivered_and_keeps_it_in_the_mean(tmp_path, monkeypatch, short_a):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": _policy(1)}, prof=_prof("zero"))
    det = stats["m"]["scorecard"]["ladder"]["work"]["a"]
    assert stats["m"]["scorecard"]["parts"]["work"]["a"] == pytest.approx(5.0 * 3 / 4)     # 3 of 4 right, one 0
    dn = det["did_not_finish"]
    assert {k: dn[k] for k in ("n", "of", "ceiling")} == {"n": 1, "of": 4, "ceiling": CEIL}
    assert dn["finished_share"] == pytest.approx(1.0)                  # the three that finished, display only
    assert det["cut"]["cut"] == 0                                                          # not an omitted cut
    assert "1 of 4 replies did not finish within 1,000 tokens and score 0 (5.00 on the 3 that finished)" in row(md, "m")[-2]


def test_zero_grades_the_visible_answer_a_cut_reply_delivered(tmp_path, monkeypatch, short_a):
    # the reply is cut at the full budget but its visible text is the right answer: it earns what its content earns
    def pol(lv, cid):
        return cut("PASS", n=CEIL) if cid == "FLAT-xa-1-1" or cid.startswith("FLAT-xa-1") else "PASS"
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": pol}, prof=_prof("zero"))
    # graded as delivered: the right visible answer earns full credit, so that unit is no "ran out" at all
    assert stats["m"]["scorecard"]["parts"]["work"]["a"] == pytest.approx(5.0)
    assert not stats["m"]["scorecard"]["ladder"]["work"]["a"].get("did_not_finish")


def test_zero_keeps_a_part_measured_where_omit_would_print_not_measured(tmp_path, monkeypatch, short_a):
    _md, om, _ = board(tmp_path, monkeypatch, {"m": _policy(2)}, prof=_prof("omit"))
    assert om["m"]["scorecard"]["ladder"]["work"]["a"]["not_measured"] is True
    _md, zr, _ = board(tmp_path, monkeypatch, {"m": _policy(2)}, prof=_prof("zero"))
    det = zr["m"]["scorecard"]["ladder"]["work"]["a"]
    assert not det.get("not_measured") and det["did_not_finish"]["n"] == 2
    assert zr["m"]["scorecard"]["parts"]["work"]["a"] == pytest.approx(5.0 * 2 / 4)


def test_zero_leaves_a_cut_below_the_full_ceiling_under_omit(tmp_path, monkeypatch, short_a):
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": _policy(0, n_below=1)}, prof=_prof("zero"))
    det = stats["m"]["scorecard"]["ladder"]["work"]["a"]
    assert det["cut"]["cut"] == 1 and not det.get("did_not_finish")
    assert stats["m"]["scorecard"]["parts"]["work"]["a"] == pytest.approx(5.0)


def test_zero_does_not_touch_a_part_that_is_not_short_keyed(tmp_path, monkeypatch):
    # work/a is not in SHORT_KEYED_PARTS here: the full-budget cut is omitted as before
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": _policy(1)}, prof=_prof("zero"))
    det = stats["m"]["scorecard"]["ladder"]["work"]["a"]
    assert det["cut"]["cut"] == 1 and not det.get("did_not_finish")


def test_the_shipped_profile_says_zero_and_names_the_five_parts():
    import yaml
    from pathlib import Path
    prof = yaml.safe_load((Path(__file__).parent.parent / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
    assert prof.get("cut_at_full_budget") == "zero"
    assert chains.SHORT_KEYED_PARTS == {"work/c2_trace", "work/c2_predict", "chat/h2_state", "chat/h2_ledger",
                                        "chat/h6_know"}


def test_the_budget_actually_sent_decides_not_a_literal():
    r = {"finish_reason": "length", "truncated": True, "max_tokens_sent": 71680, "metrics": {"completion_tokens": 71000}}
    assert chains.full_budget_cut(r, r["max_tokens_sent"]) is True            # 99.1% of what was sent
    assert chains.full_budget_cut(r, 100000) is False


# Predict is a case part (the code bank): the same rule on report.case_part_rows
def _bank_rows(n, n_full, n_below=0, ceiling=CEIL):
    out = []
    for i in range(n):
        r = {"case_id": f"P{i}", "repeat": 1, "turn": None, "response": "x", "finish_reason": "stop",
             "metrics": {"completion_tokens": 40}}
        if i < n_full:
            r.update(finish_reason="length", truncated=True, response="half", metrics={"completion_tokens": ceiling})
        elif i < n_full + n_below:
            r.update(finish_reason="length", truncated=True, response="half",
                     metrics={"completion_tokens": ceiling // 2})
        out.append(r)
    return out


def test_case_part_zero_keeps_full_budget_cuts_scored(monkeypatch):
    rows, rec = report.case_part_rows(_bank_rows(5, 2, 1), "exclude", cut_at_full_budget="zero", full_ceiling=CEIL)
    assert {k: rec["did_not_finish"][k] for k in ("n", "of", "ceiling")} == {"n": 2, "of": 5, "ceiling": CEIL}
    assert rec["ran_out"] == 1 and len(rows) == 4          # the provider-capped one leaves the mean, 2 stay
    assert not rec["not_measured"]


def test_case_part_omit_is_today_s_rule():
    rows, rec = report.case_part_rows(_bank_rows(5, 2, 1), "exclude", cut_at_full_budget="omit", full_ceiling=CEIL)
    assert rec["ran_out"] == 3 and "did_not_finish" not in rec and rec["not_measured"] is True and rows == []
    rows2, rec2 = report.case_part_rows(_bank_rows(5, 2, 1), "exclude")
    assert rec2 == rec and rows2 == rows


def test_the_part_prints_both_figures():
    dn = {"n": 2, "of": 5, "ceiling": 65536, "finished_share": 0.72}
    assert report.dnf_text(dn, 1.25) == ("2 of 5 replies did not finish within 65,536 tokens and score 0 "
                                         "(0.90 on the 3 that finished)")
    hl = {"S": {"work": 8.0}, "pts": {"work/p": 0.66}, "did_not_finish": {"work/p": dn},
          "unscaled": {"work": {"of": 9.0, "pending": []}}}
    d = {"key": "work", "label": "Coding", "in_total": True, "parts": [{"key": "p", "short": "Traps", "label": "T",
                                                                       "weight": 1.25}]}
    assert "Traps 0.66 of 1.25: 2 of 5 replies did not finish within 65,536 tokens and score 0 (0.90 on the 3 that finished)" \
        in report.points_cell(hl, d)


def test_a_did_not_finish_reply_is_never_also_left_out():
    txt = report.efficiency_cell({"efficiency": {"rate": 0.97, "n": 70, "ran_out": 4, "excluded": 2,
                                                  "did_not_finish": 2, "escalated": 0}})
    assert "2 left out of the score" in txt and "2 did not finish (graded as delivered" in txt
    assert "counted failed" not in txt                      # 4 = 2 left out + 2 did not finish: nothing double counted
    only = report.efficiency_cell({"efficiency": {"rate": 0.97, "n": 70, "ran_out": 2, "excluded": 0,
                                                    "did_not_finish": 2}})
    assert "left out" not in only and "2 did not finish" in only


def test_the_did_not_finish_points_are_the_unrounded_figure():
    # the headline keeps pts rounded to one decimal; the two-decimal did-not-finish cell must not print 0.7 as 0.70
    dn = {"n": 2, "of": 5, "ceiling": 65536, "finished_share": 0.7166666666666667}
    hl = {"S": {"work": 8.2}, "P": {"work/p": 0.65625}, "pts": {"work/p": 0.7}, "did_not_finish": {"work/p": dn},
          "unscaled": {"work": {"of": 10.0, "pending": []}}}
    d = {"key": "work", "label": "Coding", "in_total": True, "parts": [{"key": "p", "short": "Traps", "label": "T",
                                                                       "weight": 1.25}]}
    cell = report.points_cell(hl, d)
    assert "Traps 0.66 of 1.25: 2 of 5" in cell and "0.70" not in cell, cell


def test_the_efficiency_cell_says_the_did_not_finish_replies_are_the_ones_that_ran_out():
    txt = report.efficiency_cell({"efficiency": {"rate": 0.97, "n": 70, "ran_out": 2, "excluded": 0,
                                                  "did_not_finish": 2}})
    assert txt == "97% (2 ran out of budget: 2 did not finish (graded as delivered, kept in the mean))", txt


def test_dnf_text_says_score_0_only_when_every_such_reply_earned_0():
    dn = {"n": 2, "of": 5, "ceiling": 65536, "finished_share": 0.72, "all_zero": True}
    assert "and score 0 (" in report.dnf_text(dn, 1.25)
    dn = {**dn, "all_zero": False}
    t = report.dnf_text(dn, 1.25)
    assert "score 0" not in t and "and are scored on what they delivered (0.90 on the 3 that finished)" in t, t


def test_chain_part_did_not_finish_carries_all_zero(tmp_path, monkeypatch, short_a):
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": _policy(1)}, prof=_prof("zero"))
    dn = stats["m"]["scorecard"]["ladder"]["work"]["a"]["did_not_finish"]
    assert dn["all_zero"] is True
