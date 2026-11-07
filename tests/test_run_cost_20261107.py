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


# ------------------------------------------- review fix pass (2026-11-07)

def _call_with_empty_retry():
    from types import SimpleNamespace
    from crucibleforge.api import ChatResult
    replies = [ChatResult(response_text="", finish_reason="stop", prompt_tokens=100,
                          completion_tokens=40, gen_s=2.0),
               ChatResult(response_text="answer", finish_reason="stop", prompt_tokens=100,
                          completion_tokens=60, gen_s=3.0)]
    ctx = SimpleNamespace(extra_body=None, recovery_enabled=False, budget=lambda m: m,
                          model_id="m", _chat=lambda msgs, **kw: replies.pop(0),
                          _overflowed=lambda r: False, _stopped_without_answer=lambda r: False)
    return runner._Ctx.call(ctx, [{"role": "user", "content": "x"}], max_tokens=10)


def test_empty_retry_keeps_speed_metrics_clean_and_bills_the_first_call():
    r = _call_with_empty_retry()
    assert (r.prompt_tokens, r.completion_tokens) == (100, 60)       # tok/s inputs untouched
    assert (r.billed_extra_prompt_tokens, r.billed_extra_completion_tokens) == (100, 40)
    m = runner._metrics(r)
    assert m["completion_tokens"] == 60 and m["billed_extra_completion_tokens"] == 40
    ms = runner._metrics_sum([r, r])
    assert ms["completion_tokens"] == 120 and ms["billed_extra_completion_tokens"] == 80
    assert ms["tok_per_s"] == pytest.approx(120 / 6.0, abs=0.01)      # extras never in tok/s


def test_estimated_cost_includes_billed_extra_tokens():
    mt = {"prompt_tokens": 1000, "completion_tokens": 500,
          "billed_extra_prompt_tokens": 1000, "billed_extra_completion_tokens": 500}
    r = report.run_cost("m", [{"case_id": CHAT, "grade": "pass", "metrics": mt}], CFG, {}, SIDES)
    assert r["usd"] == pytest.approx(0.004)                          # twice _row()'s 0.002


def test_perf_rows_are_not_in_the_main_cost_but_are_in_spent():
    perf = _row(category="perf", cost=0.3)
    chat = _row(cost=0.2)
    assert report.run_cost("m", [perf, chat], CFG, {}, SIDES)["main_usd"] == pytest.approx(0.2)
    assert report.spent_cost("m", [perf, chat], CFG, {}) == pytest.approx(0.5)


def test_spent_is_estimated_when_no_cost_was_recorded_and_none_when_unpriceable():
    assert report.spent_cost("m", [_row(), _row()], CFG, {}) == pytest.approx(0.004)
    assert report.spent_cost("m", [_row()], {"models": []}, {}) is None
    assert report.spent_cost("m", [_row(cost=0.1), _row()], CFG, {}) == pytest.approx(0.102)


def test_cost_cell_zero_and_small_and_tilde_combinations():
    assert report.cost_cell({"cost_main_usd": 0}) == "$0.00"
    assert report.cost_cell({"cost_main_usd": 0.004999}) == "<$0.01"
    assert report.cost_cell({"cost_main_usd": 0.005}) == "$0.01"
    assert report.cost_cell({"cost_main_usd": 0.001, "cost_basis": "list", "cost_unknown_rows": 1}) == "~<$0.01*"
    assert report.cost_cell({"cost_main_usd": 0, "cost_basis": "list"}) == "~$0.00"


def test_script_js_formats_zero_exactly():
    from pathlib import Path
    js = (Path(report.__file__).parent / "templates/board/script.js").read_text(encoding="utf-8")
    assert "v === 0 ? '$0.00'" in js and "'<$0.01'" in js


def test_sanity_trips_when_the_first_replies_are_all_blocked():
    st = {"sanity_total": 0, "sanity_empty": 0, "block_guard": True}
    blocked = [{"finish_reason": "content_filter", "response": ""} for _ in range(3)]
    with pytest.raises(runner.ModelRunError, match="blocked by the provider's content filter"):
        runner._sanity_check(st, blocked, "m")


def test_sanity_does_not_trip_on_a_mix_of_blocked_and_answered():
    st = {"sanity_total": 0, "sanity_empty": 0, "block_guard": True}
    rows = [{"finish_reason": "content_filter", "response": ""},
            {"finish_reason": "stop", "response": "hi"},
            {"finish_reason": "content_filter", "response": ""}]
    runner._sanity_check(st, rows, "m")                              # must not raise
    # blocks AFTER the first N never trip the guard
    runner._sanity_check(st, [{"finish_reason": "content_filter", "response": ""}] * 5, "m")


def _stub_run(monkeypatch, unpriced):
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "_refuse_fresh_with_filter", lambda a: None)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda a, c: (c, [{"id": "x", "category": "coding"}], {}))
    monkeypatch.setattr(cli, "resolve_models", lambda c, m: [{"name": "h1"}])
    monkeypatch.setattr(cli, "_unpriced_hosted", lambda c, e: unpriced)

    class Reached(Exception):
        pass

    def boom(*a, **k):
        raise Reached
    monkeypatch.setattr(cli, "_fail_fast", boom)
    return SimpleNamespace, Reached


def _run_args(**kw):
    import argparse
    return argparse.Namespace(cmd="run", models="h1", categories=None, difficulty=None, cases=None,
                              fresh=False, judge=None, **kw)


def test_cmd_run_refuses_an_unpriced_hosted_model_and_allows_it_with_the_flag(monkeypatch):
    _, Reached = _stub_run(monkeypatch, ["h1"])
    with pytest.raises(SystemExit, match="--allow-unpriced"):
        cli.cmd_run(_run_args(), {})
    with pytest.raises(Reached):                                     # got past the price gate
        cli.cmd_run(_run_args(allow_unpriced=True), {})


def test_bench_all_has_the_flag_and_passes_it_to_cmd_run(monkeypatch):
    import inspect
    from crucibleforge import bench_all
    assert "allow_unpriced=bool(getattr(args, \"allow_unpriced\"" in inspect.getsource(bench_all.execute)
    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda p=None: {})
    monkeypatch.setattr(cli, "cmd_bench_all", lambda a, c: seen.setdefault("a", a) and 0)
    with pytest.raises(SystemExit):
        cli.main(["bench-all", "--allow-unpriced"])
    assert seen["a"].allow_unpriced is True


def test_blocked_guard_is_off_for_a_rerun_of_a_label_that_already_has_rows(tmp_path, monkeypatch):
    # the guard flag is derived from the label's transcript file (first generation only)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    assert not config.transcript_path("m").exists()
    config.transcript_path("m").write_text("{}\n", encoding="utf-8")
    assert config.transcript_path("m").stat().st_size > 0
    st = {"sanity_total": 0, "sanity_empty": 0, "block_guard": False}
    runner._sanity_check(st, [{"finish_reason": "content_filter", "response": ""}] * 3, "m")


def test_all_rows_without_usage_count_as_unknown_not_free():
    bad = _row(cost=0.2, p=0, c=0)
    r = report.run_cost("m", [bad, dict(bad), dict(bad)], CFG, {}, SIDES)
    assert r["usd"] is None and r["unknown_rows"] == 3
    assert report.cost_cell({"cost_main_usd": None, "cost_unknown_rows": 3}) == "?*"
    assert report.cost_cell({"cost_main_usd": None, "cost_unknown_rows": 0}) == "—"
    assert "3 rows without usage data" in report.notes_cell({"cost_unknown_rows": 3})
    assert "1 row without usage data" in report.notes_cell({"cost_unknown_rows": 1})
