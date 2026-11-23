"""Assistant basics (checked), step 3: the score. Share correct with a margin from items x repeats, a band word and a
ceiling mark; the same truncation, missing-row and "not run" rules as every other part; it never enters a total, a
rank, a sort or a pack gate (that part is proved against a stored real board in test_assistant_excluded_*)."""
from __future__ import annotations

import math

import pytest

from crucibleforge import assistant45, profiles, scoring45

IDS = ["IZ02-json-single-line-typed", "IZ04-lowercase-except-http", "IZ05-five-dash-lines-exact-26",
       "IZ06-allcaps-recall-notice", "IZ07-json-city-record", "IZ08-two-questions-only",
       "RX02-knights-knaves", "RX08-water-jugs", "RX13-recurrence-term", "RX20-five-houses-four-attributes",
       "RX21-flawed-cost-chain", "LX2-multihop-16k", "LX3-aggregate-12k", "LX4-count-6k", "LX6-absent-12k"]


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.delenv("CF_COMPOSITION", raising=False)
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    # the scorer's mechanics on two repeats per item (the shipped composition runs one since step 2, 2026-11-21:
    # test_assistant_profile_20261119 pins that; compute reads the count from this block)
    p["assistant"] = {**p["assistant"], "repeats": 2}
    return p


def _cat(cid):
    return {"IZ": "instruct", "RX": "reasoning", "LX": "longctx"}[cid[:2]]


def row(cid, rep, ok=True, *, share=None, cut=False, text="answer", finish="stop", **kw):
    r = {"case_id": cid, "category": _cat(cid), "repeat": rep, "grade": "pass" if ok else "fail",
         "response": text, "finish_reason": "length" if cut else finish, "truncated": cut}
    if share is not None:
        n = 8
        k = round(share * n)
        r["grade"] = "pass" if k == n else "fail"
        r["elements"] = {"results": [{"id": f"c{i}", "pass": i < k} for i in range(n)]}
    r.update(kw)
    return r


def rows_of(fn):
    return [fn(cid, rep) for cid in IDS for rep in (1, 2)]


def test_every_run_right_is_a_pass_with_a_margin(prof):
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r)))
    assert a["status"] == "ok" and a["share"] == 1.0 and a["band"] == "pass"
    assert a["n_items"] == 15 and a["n_runs"] == 30 and a["cut"] == 0
    # the interval never collapses to nothing at the ceiling: 15 items cannot show more than ~0.9 certainty
    assert 0.03 < a["margin"] < 0.12


def test_the_share_and_margin_come_from_items_and_repeats(prof):
    # 12 items right twice, 3 items right once: item means 1 x12, 0.5 x3 -> share 0.9
    bad = set(IDS[:3])
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=not (c in bad and r == 2))))
    assert a["share"] == pytest.approx(0.9)
    means = [1.0] * 12 + [0.5] * 3
    sd = math.sqrt(sum((m - 0.9) ** 2 for m in means) / 14)
    se = max(sd / math.sqrt(15), math.sqrt(0.9 * 0.1 / 15))
    assert a["margin"] == pytest.approx(1.645 * se)
    assert a["band"] == "pass"


def test_instruction_rows_score_by_element_and_the_rest_by_the_answer(prof):
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, share=0.875) if c.startswith("IZ") else row(c, r)))
    assert a["share"] == pytest.approx((6 * 0.875 + 9) / 15)


@pytest.mark.parametrize("share,band", [(1.0, "pass"), (0.85, "pass"), (0.8499, "partial"), (0.70, "partial"),
                                         (0.45, "partial"), (0.40, "partial"), (0.3999, "weak"), (0.0, "weak")])
def test_band_edges_sit_inside_the_gaps_of_the_evidence(prof, share, band):
    # evidence: sound 0.93-1.00, damaged 0.45-0.70, small or old 0.00-0.35
    assert assistant45.band(share, prof["assistant"]["bands"]) == band
    assert prof["assistant"]["bands"] == {"pass": 0.85, "partial": 0.40}


def test_a_cut_reply_is_left_out_and_counted(prof):
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=False, cut=True, text="partial thought")
                                          if (c, r) == ("RX13-recurrence-term", 1) else row(c, r)))
    assert a["status"] == "ok" and a["cut"] == 1 and a["share"] == 1.0 and a["n_runs"] == 29


def test_more_than_a_quarter_cut_is_not_measured(prof):
    cut_ids = set(IDS[:8])        # 16 of 30 runs
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=False, cut=True, text="x") if c in cut_ids
                                          else row(c, r)))
    assert a["status"] == "not_measured" and a["share"] is None and a["cut"] == 16
    # two cut of thirty is a left-out pair, not a verdict; at least two are needed as well
    one = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=False, cut=True, text="x")
                                            if (c, r) == (IDS[0], 1) else row(c, r)))
    assert one["status"] == "ok"
    two = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=False, cut=True, text="x")
                                            if c == IDS[0] else row(c, r)))
    assert two["status"] == "ok" and two["cut"] == 2


def test_a_missing_run_is_pending_never_a_zero(prof):
    rows = [r for r in rows_of(lambda c, r: row(c, r)) if (r["case_id"], r["repeat"]) != (IDS[3], 2)]
    a = assistant45.compute(prof, rows)
    assert a["status"] == "pending" and a["share"] is None and a["missing"] == 1
    a = assistant45.compute(prof, rows, [{"case_id": IDS[3], "repeat": 2, "reason": "error"}])
    assert a["status"] == "pending" and a["missing"] == 1


def test_infrastructure_empty_and_filtered_replies_are_missing_runs(prof):
    rows = rows_of(lambda c, r: row(c, r))
    for r in rows:
        if (r["case_id"], r["repeat"]) == (IDS[0], 1):
            r.update(response="", grade="fail", finish_reason="stop")                  # a dropped stream
        if (r["case_id"], r["repeat"]) == (IDS[1], 1):
            r.update(response="", grade="fail", finish_reason="content_filter")        # a provider filter
        if (r["case_id"], r["repeat"]) == (IDS[2], 1):
            r.update(response="", grade="error", finish_reason=None)                   # a transport error
    kept, missing = scoring45.split_missing({1: rows})
    a = assistant45.compute(prof, kept[1], missing)
    assert a["status"] == "pending" and a["missing"] == 3 and a["share"] is None


def test_a_refusal_in_words_is_a_wrong_answer_and_an_empty_reply_after_thinking_is_zero(prof):
    rows = rows_of(lambda c, r: row(c, r))
    for r in rows:
        if r["case_id"] == IDS[7]:
            r.update(response="I can't help with that.", grade="fail")
        if r["case_id"] == IDS[8]:
            r.update(response="", grade="fail", reasoning="thought about it", metrics={"reasoning_tokens": 900})
    kept, missing = scoring45.split_missing({1: rows})
    a = assistant45.compute(prof, kept[1], missing)
    assert a["status"] == "ok" and a["share"] == pytest.approx(13 / 15)


def test_no_rows_is_not_run(prof):
    assert assistant45.compute(prof, [])["status"] == "not_run"
    assert assistant45.compute(prof, [{"case_id": "CC4-second-pitch", "category": "rpdeep", "grade": "pass"}]
                               )["status"] == "not_run"


def test_a_profile_without_the_block_has_no_column():
    assert assistant45.compute({"cases": {}}, [row(IDS[0], 1)]) is None


def test_the_cell_wording(prof):
    a = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=not (c in IDS[:2] and r == 1))))
    assert a["share"] == pytest.approx(28 / 30)
    cell = assistant45.cell(a, validated=True, ceiling=False)
    assert cell.startswith("pass 93% ± ") and "‡" not in cell
    assert assistant45.cell(a, validated=True, ceiling=True).endswith("‡")
    assert assistant45.cell(a, validated=False, ceiling=False) == "pending validation"
    assert assistant45.cell(None, validated=True, ceiling=False) == "not run"
    assert assistant45.cell(assistant45.compute(prof, []), validated=True, ceiling=False) == "not run"
    cut = assistant45.compute(prof, rows_of(lambda c, r: row(c, r, ok=False, cut=True, text="x")
                                            if c == IDS[0] else row(c, r)))
    assert assistant45.cell(cut, validated=True, ceiling=False).endswith("(2 cut)")
    nm = {"status": "not_measured", "cut": 16}
    assert assistant45.cell(nm, validated=True, ceiling=False) == "not measured"
    pend = assistant45.compute(prof, rows_of(lambda c, r: row(c, r))[:-1])
    assert assistant45.cell(pend, validated=True, ceiling=False).startswith("pending")


def test_the_ceiling_mark_follows_the_boards_rule(prof):
    def st(share, **k):
        return {"assistant45": {"status": "ok", "share": share}, **k}
    stats = {"a": st(0.97), "b": st(0.95), "c": st(0.40)}
    assert assistant45.ceiling(["a", "b"], stats) is True
    assert assistant45.ceiling(["a", "b", "c"], stats) is False
    assert assistant45.ceiling(["a"], stats) is False                       # one model alone never marks a ceiling
    assert assistant45.ceiling(["a", "b"], {**stats, "b": st(0.95, validation_cc=True)}) is False
    assert assistant45.ceiling(["a", "b"], {**stats, "b": st(0.95, era={"old": True})}) is False
