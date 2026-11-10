"""4.1 release blocker F1: a hosted provider that will not bill the request
(credit spent, spend limit, suspended account) never decides a score.  Its
rows are not failures, never count toward the attempt limit, and the bonus
pack they belong to reads "pending" (no Total, unranked) instead of 0."""
from __future__ import annotations

import json

import pytest

from crucibleforge import bench_all, preflight, report
from test_points_board_39 import (_board, _ladder_rows, _row, _rows, _set_file,
                                  _setup, _work_only)

PACK = ["CZ08-triple-substring", "CZ09-range-kth", "CZ11-lex-triple-substring"]

BILLING_MESSAGES = {
    "anthropic": 'request rejected: HTTP 400: {"error":{"code":"invalid_request_error",'
                 '"message":"Your credit balance is too low to access the API"}}',
    "xai": 'transport failure: HTTP 402: {"code":"personal-team-blocked:spending-limit",'
           '"error":"You have run out of credits"}',
    "moonshot": 'transport failure: HTTP 429: {"error":{"message":"Your account is '
                'suspended due to insufficient balance"}}',
    "deepseek": 'transport failure: HTTP 402: {"error":{"message":"Insufficient Balance"}}',
}


def _err(msg, kind="transport"):
    return {"grade": "error", "error_kind": kind, "error": msg}


def _billing_pack_rows(msg=BILLING_MESSAGES["anthropic"], kind="transport"):
    out = []
    for cid in PACK:
        for rep in (1, 2):
            out.append(_row(cid, "coding", rep, False, run="lad", grade="error",
                            error=msg, error_kind=kind))
    return out


# (a) ----------------------------------------------------------------------

@pytest.mark.parametrize("who", sorted(BILLING_MESSAGES))
def test_each_provider_wording_is_billing(who):
    assert preflight.billing_error_row(_err(BILLING_MESSAGES[who]))


@pytest.mark.parametrize("row", [
    _err("transport failure: ReadTimeout after 600 s"),
    _err("transport failure: HTTP 503: upstream unavailable"),
    _err("transport failure: HTTP 400: context length exceeded"),
    _err(BILLING_MESSAGES["xai"], kind="generation"),
    {"grade": "fail", "error": BILLING_MESSAGES["xai"], "error_kind": "transport"},
    {"grade": "pass"},
])
def test_other_failures_are_not_billing(row):
    assert not preflight.billing_error_row(row)


# (b) ----------------------------------------------------------------------

def test_a_billing_errored_pack_is_pending_not_zero_and_the_model_is_unranked(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    st = _board(env, {"blocked": _rows() + _billing_pack_rows(),
                      "clean": _rows() + _ladder_rows({c: 2 for c in PACK})})
    b, c = st["blocked"], st["clean"]
    card = b["scorecard"]
    det = card["ladder"]["work"]["code"]
    assert det["sets"][0]["state"] == "pending" and det["sets"][0]["share"] is None
    assert "work" in card["partial"] and card["total"] is None
    assert card["parts"]["work"]["code"] == pytest.approx(3.0)       # standard only
    assert "(packs pending)" in card["labels"]["work"]
    assert b["provisional"] is True
    assert b["provisional_reason"].startswith("Bonus packs pending a re-run")
    # the same model without billing rows is untouched
    assert c["scorecard"]["total"] == pytest.approx(11.0)
    assert not c["provisional"]


def test_a_billing_errored_adult_pack_blanks_only_its_own_score(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "nsfw/uncensored", 1, PACK)
    st = _board(env, {"m": _rows() + _billing_pack_rows()})
    card = st["m"]["scorecard"]
    assert card["nsfw"] is None and "nsfw" in card["partial"]
    assert card["total"] is not None              # NSFW is outside the Total
    assert not st["m"]["provisional"]


# (c) ----------------------------------------------------------------------

def test_a_non_billing_transport_error_still_counts_failed(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    rows = _billing_pack_rows(msg="transport failure: HTTP 503: upstream unavailable")
    st = _board(env, {"m": _rows() + rows})["m"]
    det = st["scorecard"]["ladder"]["work"]["code"]
    assert det["sets"][0]["state"] == "scored" and det["sets"][0]["share"] == 0.0
    assert st["scorecard"]["total"] is not None and not st["provisional"]
    assert "packs_blocked" not in st


# (d) ----------------------------------------------------------------------

def test_billing_rows_never_exhaust_the_attempt_limit_and_the_pack_is_due(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    _board(env, {"m": _rows() + _billing_pack_rows() + _billing_pack_rows()})
    att = bench_all.error_attempts("m")
    assert not any(c in att for c in PACK)
    due = bench_all.ladder_due(env.cfg, "m", att)
    assert due and due["jobs"] >= len(PACK)
    # a generic transport error is still counted toward finality
    _board(env, {"g": _rows() + _billing_pack_rows(msg="transport failure: HTTP 503")
                 + _billing_pack_rows(msg="transport failure: HTTP 503")})
    assert all(bench_all.error_attempts("g").get(c, 0) >= bench_all.MAX_CASE_ATTEMPTS
               for c in PACK)


# (e) ----------------------------------------------------------------------

def test_labels_without_billing_rows_render_identically(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    models = {"clean": _rows() + _ladder_rows({c: 2 for c in PACK}),
              "fail": _rows(fail_cases={"CB-B5-famcal-rrule"}) + _ladder_rows({c: 1 for c in PACK})}
    with_fix = json.dumps(_board(env, models), sort_keys=True, default=str)
    by_t = {1: [], 2: []}
    assert report._drop_billing_jobs({}, by_t)[0] is by_t         # no billing row: same object
    monkeypatch.setattr(report, "_drop_billing_jobs",
                        lambda prof, by_t: (by_t, set(), 0))
    without = json.dumps(_board(env, models), sort_keys=True, default=str)
    assert with_fix == without
