"""Run cost + provider content-filter marking (2026-11-07, MakoBench round).

One run of the standard Code + Chat tests priced at the provider's rate;
NSFW cost and blocked counts are kept apart; rows without a bill are
estimated from tokens at list price and marked.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import cli, config, report, runner

NSFW = "WL2-lighthouse"      # a case of the in_total:false NSFW score
CHAT = "CC3-tea-hut"         # a Chat case
SIDES = {NSFW: "separate", CHAT: "main"}


def _row(case=CHAT, p=1000, c=500, cost=None, **kw):
    r = {"case_id": case, "grade": "pass", "metrics": {"prompt_tokens": p, "completion_tokens": c}}
    if cost is not None:
        r["cost_usd"] = cost
    r.update(kw)
    return r


PRICE = {"price": {"input": 1.0, "output": 2.0}}
CFG = {"models": [{"name": "m", **PRICE}]}


def test_real_profile_sides():
    s = report._case_sides(None)
    assert s[NSFW] == "separate" and s[CHAT] == "main"


def test_recorded_cost_is_billed():
    r = report.run_cost("m", [_row(cost=0.25), _row(cost=0.5)], CFG, {}, SIDES)
    assert r["usd"] == pytest.approx(0.75) and r["basis"] == "billed"
    assert r["estimated_rows"] == 0 and r["unknown_rows"] == 0


def test_estimated_from_tokens_when_no_recorded_cost():
    r = report.run_cost("m", [_row()], CFG, {}, SIDES)       # 1000*1 + 500*2 per 1e6
    assert r["usd"] == pytest.approx(0.002) and r["basis"] == "list" and r["estimated_rows"] == 1


def test_mixed_basis():
    r = report.run_cost("m", [_row(cost=0.1), _row()], CFG, {}, SIDES)
    assert r["basis"] == "mixed" and r["estimated_rows"] == 1


def test_declared_basis_list_wins_over_recorded_costs():
    cfg = {"models": [{"name": "m", "price": {"input": 1, "output": 2, "basis": "list"}}]}
    assert report.run_cost("m", [_row(cost=0.1)], cfg, {}, SIDES)["basis"] == "list"


def test_basis_list_in_models_yaml_applies_to_a_run_whose_meta_predates_it():
    cfg = {"models": [{"name": "m", "price": {"input": 1, "output": 2, "basis": "list"}}]}
    meta = {"price": {"input": 1, "output": 2}}
    assert report.run_cost("m", [_row(cost=0.1)], cfg, meta, SIDES)["basis"] == "list"


def test_transport_error_row_is_unknown_not_free():
    bad = {"case_id": CHAT, "grade": "error", "cost_usd": 0.0, "metrics": {}}
    r = report.run_cost("m", [_row(cost=0.3), bad], CFG, {}, SIDES)
    assert r["usd"] == pytest.approx(0.3) and r["unknown_rows"] == 1


def test_skipped_rows_are_ignored():
    sk = [{"case_id": CHAT, "skipped": True, "cost_usd": 0.0, "metrics": {}},
          {"case_id": CHAT, "grade": "skipped", "metrics": {}}]
    r = report.run_cost("m", [_row(cost=0.2)] + sk, CFG, {}, SIDES)
    assert r["unknown_rows"] == 0 and r["usd"] == pytest.approx(0.2)


def test_local_model_has_no_cost():
    r = report.run_cost("rig", [_row()], {"models": [{"name": "rig"}]}, {}, SIDES)
    assert r["usd"] is None and r["main_usd"] is None and r["basis"] is None


def test_meta_price_beats_models_yaml():
    meta = {"price": {"input": 10.0, "output": 0.0}}
    r = report.run_cost("m", [_row(c=0)], CFG, meta, SIDES)  # models.yaml would say 0.001
    assert r["usd"] == pytest.approx(0.01)


def test_side_split():
    r = report.run_cost("m", [_row(CHAT, cost=0.4), _row(NSFW, cost=0.1)], CFG, {}, SIDES)
    assert r["main_usd"] == pytest.approx(0.4) and r["separate_usd"] == pytest.approx(0.1)
    assert r["usd"] == pytest.approx(0.5)


def test_content_filtered_split_and_notes():
    rows = [_row(CHAT, finish_reason="content_filter"), _row(NSFW, finish_reason="content_filter"),
            _row(CHAT, finish_reason="stop"),
            {"case_id": CHAT, "skipped": True, "finish_reason": "content_filter"}]
    cf = report.content_filtered(rows, SIDES)
    assert cf == {"total": 2, "main": 1, "separate": 1}
    n = report.notes_cell({"content_filtered": cf})
    assert "2 replies blocked by the provider's content filter (a blocked reply scores zero)" in n
    assert "1 of them in NSFW (counted as refusals)" in n
    one = report.notes_cell({"content_filtered": {"total": 1, "main": 1, "separate": 0}})
    assert "1 reply blocked" in one and "NSFW" not in one
    assert "3 rows without usage data" in report.notes_cell({"cost_unknown_rows": 3})
    assert "cost" not in report.notes_cell({"cost_unknown_rows": 0})


def test_cost_cell_marks():
    assert report.cost_cell({"cost_main_usd": 11.774}) == "$11.77"
    assert report.cost_cell({"cost_main_usd": 0.42, "cost_basis": "list"}) == "~$0.42"
    assert report.cost_cell({"cost_main_usd": 0.66, "cost_unknown_rows": 2}) == "$0.66*"
    assert report.cost_cell({"cost_main_usd": 0.001}) == "<$0.01"
    assert report.cost_cell({"cost_main_usd": None}) == "—"


# --------------------------------------------------- model_stats / html_rows

def _stats(rows, meta=None, era=None, cfg=None):
    return report.model_stats("m", cfg or CFG, rows=rows, meta=meta or {}, era=era)


def test_model_stats_keys_and_main_scope():
    rows = [_row(CHAT, cost=0.4), _row(NSFW, cost=0.1, finish_reason="content_filter")]
    s = _stats(rows)
    assert s["cost_usd"] == pytest.approx(0.5)
    assert s["cost_main_usd"] == pytest.approx(0.4) and s["cost_separate_usd"] == pytest.approx(0.1)
    assert s["content_filtered"] == {"total": 1, "main": 0, "separate": 1}
    # the html row carries the main figure only; NSFW has its own key
    s["scorecard"] = report.scorecard(s, report.scoring_config(CFG))
    h = report.html_rows(["m"], {"m": s})[0]
    assert h["cost_usd"] == pytest.approx(0.4) and h["cost_separate_usd"] == pytest.approx(0.1)
    assert h["blocked"] == 0 and h["blocked_separate"] == 1 and h["cost_partial"] is False
    assert h["cost_basis"] == "billed"


def test_old_era_has_no_cost():
    s = _stats([_row(CHAT, cost=0.4)], era={"old": True})
    assert s["cost_usd"] is None and s["cost_main_usd"] is None and s["cost_basis"] is None


def test_bootstrap_skips_cost():
    with report.boot_mode():
        s = _stats([_row(CHAT, cost=0.4, finish_reason="content_filter")])
    assert s["cost_usd"] is None and s["content_filtered"]["total"] == 0


# ----------------------------------------------------------------- markdown

def test_footnote_and_column_only_when_something_is_priced():
    stats = {"a": {"cost_main_usd": 1.5}, "b": {"cost_main_usd": None}}
    assert report._has_cost(["a", "b"], stats) and not report._has_cost(["b"], stats)
    assert "Run cost = what the standard Code and Chat tests" in report.COST_FOOTNOTE


# ------------------------------------------------------------------- runner

def test_sanity_ignores_provider_blocked_replies():
    st = {"sanity_total": 0, "sanity_empty": 0}
    blocked = [{"finish_reason": "content_filter", "response": ""} for _ in range(3)]
    runner._sanity_check(st, blocked, "m")          # must not raise
    assert st["sanity_total"] == 0


def test_sanity_still_trips_on_truly_empty_replies():
    st = {"sanity_total": 0, "sanity_empty": 0}
    empty = [{"finish_reason": "stop", "response": ""} for _ in range(3)]
    with pytest.raises(runner.ModelRunError, match="all empty"):
        runner._sanity_check(st, empty, "m")


def test_unpriced_hosted_is_listed(monkeypatch):
    class P:
        def __init__(self, t): self.type = t
    monkeypatch.setattr("crucibleforge.providers.provider_for",
                        lambda cfg, e: P("openai" if e["name"].startswith("h") else "studioforge"))
    es = [{"name": "h1"}, {"name": "h2", "price": {"input": 0, "output": 0}},
          {"name": "rig"}]
    assert cli._unpriced_hosted({}, es) == ["h1"]
