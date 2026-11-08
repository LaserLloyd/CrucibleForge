"""Board-time quote re-verification (2026-11-08): stored false negatives no
longer cost points. Deterministic, no judge call, every flip logged."""
from __future__ import annotations

import logging

from crucibleforge import report, rpdeep

TEXT = "The ledger was opened on **23 February** by the clerk. Later the gate closed."
QUOTE = "The ledger was opened on 23 February by the clerk"
SINGLE = "judge said yes but its quote is not in the reply"
MULTI = ("judge said yes but only {k}/{n} samples quoted the reply verbatim "
         "(quote not in the reply)")


def _row(results, **kw):
    row = {"case_id": "ZZ1", "model_label": "m", "repeat": 1, "category": "steer",
           "response": TEXT,
           "judge": {"elements": {"results": results, "rate": 0.0}}}
    row.update(kw)
    return row


def _el(detail, quote=QUOTE, **kw):
    e = {"id": "e1", "type": "judge", "pass": False, "judge_pass": True,
         "quote": quote, "detail": detail}
    e.update(kw)
    return e


def _re(row, monkeypatch, case=None):
    monkeypatch.setattr(report, "_cases_by_id", lambda: {"ZZ1": case or {}})
    return report._recheck([row])[0]


def _res(row):
    return row["judge"]["elements"]["results"]


def test_single_sample_wording_flips(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        out = _re(_row([_el(SINGLE)]), monkeypatch)
    assert _res(out)[0]["pass"] is True
    assert out["judge"]["elements"]["rate"] == 1.0
    assert any("ZZ1" in r.getMessage() and "m" in r.getMessage() and "e1" in r.getMessage()
               and "quote recheck" in r.getMessage() for r in caplog.records)


def test_multi_sample_flips_only_when_it_tips_the_majority(monkeypatch):
    # 1 of 3 verified already; this quote makes 2 of 3 -> flips
    assert _res(_re(_row([_el(MULTI.format(k=1, n=3))]), monkeypatch))[0]["pass"]
    # 0 of 3 verified; this quote makes 1 of 3 -> no majority
    assert not _res(_re(_row([_el(MULTI.format(k=0, n=3))]), monkeypatch))[0]["pass"]
    # 1 of 2 + this = 2 of 2 -> flips; 0 of 2 + this = 1 of 2 -> tie is NO
    assert _res(_re(_row([_el(MULTI.format(k=1, n=2))]), monkeypatch))[0]["pass"]
    assert not _res(_re(_row([_el(MULTI.format(k=0, n=2))]), monkeypatch))[0]["pass"]


def test_a_quote_that_still_does_not_verify_stays_failed(monkeypatch):
    out = _re(_row([_el(SINGLE, quote="something never said in this reply")]), monkeypatch)
    assert not _res(out)[0]["pass"]


def test_a_judge_no_or_a_non_quote_failure_is_untouched(monkeypatch):
    no = _el("judge: no", judge_pass=False)
    other = _el("not in character - t1: ooc")
    out = _re(_row([no, other]), monkeypatch)
    assert [e["pass"] for e in _res(out)] == [False, False]


def test_dialogue_scoped_elements_keep_their_handling(monkeypatch):
    case = {"judge_elements": [{"id": "e1", "quote_scope": "dialogue"}]}
    out = _re(_row([_el(SINGLE)]), monkeypatch, case)
    assert not _res(out)[0]["pass"]


def test_rows_are_copied_not_mutated_and_the_recheck_is_deterministic(monkeypatch):
    row = _row([_el(SINGLE)])
    out1 = _re(row, monkeypatch)
    out2 = _re(row, monkeypatch)
    assert row["judge"]["elements"]["results"][0]["pass"] is False
    assert out1 == out2


def test_a_flip_is_logged_once_per_row_element(monkeypatch, caplog):
    row = _row([_el(SINGLE)], model_label="once")
    with caplog.at_level(logging.INFO):
        _re(row, monkeypatch)
        _re(row, monkeypatch)
    n = [r for r in caplog.records if "quote recheck" in r.getMessage() and "once" in r.getMessage()]
    assert len(n) == 1


def test_rpdeep_rows_are_left_to_rpdeep(monkeypatch):
    out = _re(_row([_el(SINGLE)], category="rpdeep"), monkeypatch)
    assert not _res(out)[0]["pass"]


def test_rpdeep_accepts_the_single_sample_wording():
    case = {"judge_elements": [{"id": "e1", "turns": [1]}]}
    # a spliced quote: only quote_found (not the verbatim short path) accepts it
    r = _el(SINGLE, quote="opened on 23 February by the clerk / Later the gate closed")
    out = rpdeep._stored_quote_ok(r, [TEXT], case)
    assert out["pass"] is True
