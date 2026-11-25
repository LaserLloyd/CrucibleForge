"""4.5.1 (owner 2026-11-24): any judge may judge a 4.5 run, local or hosted; a hosted judge is billed apart from the
model's cost; the plan price comes from measured token profiles. Fakes only: no hosted call, no rig, no network."""
from __future__ import annotations

import json
import re
from argparse import Namespace
from types import SimpleNamespace

import pytest

from crucibleforge import cli, config, judge, judge45, plan_price, profiles, providers, report, spend

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

QWEN = "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"
HOSTED = {"provider": "router", "model_id": "vendor/some-judge"}


def _cfg(**extra_providers):
    return {"providers": {"router": {"type": "openai", "base_url": "http://router.invalid/v1", "concurrency": 3},
                          "studioforge": {"type": "studioforge", "base_url": "http://rig.invalid/v1"},
                          "lm": {"type": "lmstudio", "base_url": "http://lm.invalid/v1"}, **extra_providers},
            "models": [], "defaults": {},
            "judge": {"samples": 1, "candidates": [
                {"name": "board", "provider": "studioforge", "model_id": QWEN},
                {"name": "router-judge", **HOSTED, "price": {"input": 0.5, "output": 2.0}},
                {"name": "other-hosted", "provider": "router", "model_id": "vendor/another"}]}}


@pytest.fixture
def prof():
    return profiles.load_profile("bench45")


@pytest.fixture(autouse=True)
def _no_outside_spend(monkeypatch):
    monkeypatch.setattr(spend, "outside_total_usd", lambda: 0.0)


# ================================================================== A. any judge
def test_a_hosted_judge_of_any_model_is_allowed_and_kept_alone(prof):
    args = Namespace(judge="router-judge")
    cfg = cli._guard_45_judge(args, _cfg(), prof)
    # served through its own provider entry (spend by role, review F3), named by the user's provider
    assert args.judge["model_id"] == "vendor/some-judge" and args.judge["provider"] == "router~judge"
    assert args._hosted_judge == {"model_id": "vendor/some-judge", "provider": "router", "priced": True}
    names = [c.get("name") for c in cfg["judge"]["candidates"]]
    assert names == ["board", "router-judge"]          # the hosted judge nobody named is no fallback
    assert cfg["providers"]["router~judge"]["base_url"] == cfg["providers"]["router"]["base_url"]
    with spend.judge_role("router~judge"):
        assert spend.is_judge()
    with spend.judge_role("router"):
        assert not spend.is_judge()


def test_a_local_other_judge_and_the_profile_judge_are_unchanged(prof):
    args = Namespace(judge="lm:some-local-8b")
    cli._guard_45_judge(args, _cfg(), prof)
    assert args._hosted_judge is None and "lm~judge" not in spend._JUDGE_PROVIDERS
    args = Namespace(judge=None)
    cfg = cli._guard_45_judge(args, _cfg(), prof)
    assert args._hosted_judge is None
    assert [c.get("name") for c in cfg["judge"]["candidates"]] == ["board"]


def test_the_trace_names_the_judge_its_provider_and_the_published_judge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    judge45.record_judge_provider(tmp_path, "vendor/some-judge", "router")
    assert judge45.judge_providers(tmp_path) == {"vendor/some-judge": "router"}
    t = report.other_judge_text(["vendor/some-judge"])
    assert t == ("judged by vendor/some-judge (router); MakoBench's published scores use the local "
                 "Qwen 3.8 27B Q8 judge")
    assert report.other_judge_text(["x/unknown"]).startswith("judged by x/unknown; ")
    assert judge45.judge_name(QWEN) == "Qwen 3.8 27B Q8"


def test_rows_of_another_judge_keep_todays_treatment(monkeypatch):
    # owner 2026-11-24: ranked and marked on the user's board, refused by a public render
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {"judge": {"model_id": QWEN}})
    monkeypatch.setattr(report, "active_name", lambda cfg=None: "bench45")
    row = {"judge_ladder": {"complete": True, "instrument": {"judge_model": "vendor/some-judge"}}}
    assert not any("judged by" in h for h in report.held45([row], {}, {}))
    held = report.held45([row], {}, {"_audience": report.PUBLIC})
    assert any("judged by vendor/some-judge" in h and "published scores use" in h for h in held)


# ------------------------------------------------------------------ no rig unless StudioForge is named
def _guard_provs(monkeypatch, cfg, entries, judge_spec):
    seen = []
    monkeypatch.setattr(providers.Provider, "snapshot", lambda self: seen.append(self.name) or {})
    g = cli._ProviderGuard(cfg, entries, judge=judge_spec)
    return set(g.provs), seen


@pytest.mark.parametrize("model_prov,judge_spec,want", [
    ("lm", dict(HOSTED), {"lm"}),                                         # laptop model + hosted judge
    ("router", {"provider": "studioforge", "model_id": QWEN}, {"studioforge"}),   # hosted model + local judge
    ("router", dict(HOSTED), set()),                                      # both hosted
    ("lm", {"provider": "lm", "model_id": "local-judge"}, {"lm"}),        # both local, no StudioForge named
])
def test_every_mix_touches_only_the_local_providers_it_names(  # adult page on (the default)
        monkeypatch, model_prov, judge_spec, want, tmp_path):
    cfg = _cfg()
    providers._CACHE.clear()
    entry = {"name": "m", "provider": model_prov, "model_id": "m-1"}
    provs, seen = _guard_provs(monkeypatch, cfg, [entry], judge_spec)
    assert provs == want and set(seen) == want
    # the same judge classes the adult page in every mix (fake judge, no call)
    P1 = _p1()
    res = P1._folder(tmp_path / "f", P1.std_rows(variants=("write",), n=1))
    rd = judge45.judge_reader(judge_spec["model_id"], provider=judge_spec["provider"],
                              provider_type=cfg["providers"][judge_spec["provider"]]["type"])
    st = judge45.judge_model(res, P1.LABEL, profiles.load_profile("bench45"), rd, P1.FakeJudge())
    assert st["classed"] == 1 and rd.hosted == (judge_spec["provider"] == "router")


def test_a_hosted_judge_takes_no_lease_and_no_rig_lock(monkeypatch):
    from crucibleforge import studioforge
    for fn in ("acquire_lease", "release_stale_leases", "gpu_indices", "status", "loaded_parallel"):
        monkeypatch.setattr(studioforge, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("rig call")))
    cfg = _cfg()
    providers._CACHE.clear()
    assert judge.acquire_judge_lease(cfg, override=dict(HOSTED)) is None
    jc = judge.JudgeClient(cfg, dict(HOSTED))
    assert jc.load() == 0.0 and jc.concurrency == 3          # the provider's own concurrency
    assert cli._remote_judge_only(Namespace(cmd="judge", judge=dict(HOSTED), profile="bench45"), cfg)


# ------------------------------------------------------------------ the adult page goes to any judge (owner 2026-11-24)
def _p1():
    import test_judge45_part1_20261121 as P1
    return P1


def test_nsfw_plus_classing_runs_with_a_hosted_judge_and_stores_its_votes(tmp_path):
    P1 = _p1()
    res = P1._folder(tmp_path / "f", P1.std_rows(variants=("write",), n=2))
    rd = judge45.judge_reader("vendor/some-judge", provider="router", provider_type="openai")
    fake = P1.FakeJudge(cls="SOFTENED")
    st = judge45.judge_model(res, P1.LABEL, profiles.load_profile("bench45"), rd, fake)
    assert st["classed"] == 2 and st["calls"] == fake.calls == 2 and st["judge_filtered"] == 0
    again = judge45.judge_model(res, P1.LABEL, profiles.load_profile("bench45"), rd, fake)
    assert again["calls"] == 0                                     # stored: never asked (or billed) twice


def test_judge_check_runs_with_a_hosted_judge(tmp_path):
    P1 = _p1()
    rd = judge45.judge_reader("vendor/some-judge", provider="router", provider_type="openai")
    fake = P1.FakeJudge()
    judge45.run_check(tmp_path, rd, fake)
    assert fake.calls > 0
    assert judge45.check_numbers(judge45.load_check(tmp_path, "vendor/some-judge"))


def test_no_nsfw_plus_is_the_users_own_opt_out_for_any_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [{"case_id": "A1", "category": "nsfw"}, {"case_id": "C1", "category": "rpdeep"},
            {"case_id": "C2", "category": "coding"}]
    monkeypatch.setattr(judge, "_pending_rows", lambda label, f, r, only=None, *a: list(rows))
    args = Namespace(force=False, retry_failed=False, no_nsfw_plus=True)
    assert cli._opt_out_cases(args, ["m"], None) == ["C1", "C2"]
    rows[:] = [{"case_id": "A1", "category": "nsfw"}]
    assert cli._opt_out_cases(args, ["m"], None) == [cli._NO_CASE]          # never "every case"
    monkeypatch.setattr(cli, "_adult_plan_of", lambda *a, **k: (_ for _ in ()).throw(AssertionError("planned")))
    assert cli._adult_judge_phase(args, _cfg(), profiles.load_profile("bench45"), ["m"]) == {}


def test_with_the_default_a_hosted_judge_gets_the_adult_page(monkeypatch, tmp_path, prof):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = []
    monkeypatch.setattr(cli, "_adult_plan_of", lambda a, c, p, labels: seen.append(labels) or {})
    args = Namespace(judge="router-judge")
    cli._guard_45_judge(args, _cfg(), prof)
    assert cli._adult_judge_phase(args, _cfg(), prof, ["m"]) == {} and seen == [["m"]]     # planned, not skipped


def test_the_help_says_a_hosted_judge_receives_every_reply():
    text = open(cli.__file__, encoding="utf-8").read()
    assert text.count("receives every reply it judges, NSFW+ included, unless --no-nsfw-plus is passed") == 2
    assert "a hosted judge never reads" not in text


# ------------------------------------------------------------------ a judge-side filter is never the model's
def test_a_judge_side_filter_on_the_standard_judge_leaves_the_row_pending():
    from types import SimpleNamespace as NS

    class JC:
        thinking = False
        row_timeout_s = None
        extra_body: dict = {}
        model_id = "vendor/some-judge"
        deadline = None

        def chat(self, messages, **kw):
            return NS(response_text="", finish_reason="content_filter", completion_tokens=0, reasoning_tokens=0,
                      reasoning_tail="", prompt_tokens=10, reasoning_text="", error=None)
    row = {"case_id": "C", "rubric": "rp_single", "needs_judge": True, "prompt": "p", "response": "A scene.",
           "finish_reason": "stop", "tool_calls": None}
    v = judge._judge_row(JC(), row)
    assert v["judge_failed"] is True and v.get("scores") is None and not v.get("empty_generation")
    assert judge45.judge_filtered(v)
    assert judge.willingness_score({**row, "judge": v}) is None        # pending, never W 0 for the model
    # an empty judge answer is the judge's too; a model that wrote nothing is not
    assert judge45.judge_filtered({"judge_failed": True, "judge_raw": "", "judge_finish_reason": "stop"})
    assert not judge45.judge_filtered({"judge_failed": True, "empty_generation": True, "judge_raw": ""})
    assert not judge45.judge_filtered({"judge_failed": True, "judge_raw": "", "judge_finish_reason": "length"})


def test_a_judge_side_filter_on_nsfw_plus_is_counted_and_stays_not_judged(tmp_path):
    P1 = _p1()
    res = P1._folder(tmp_path / "f", P1.std_rows(variants=("write",), n=2))
    rd = judge45.judge_reader("vendor/some-judge", provider="router", provider_type="openai")

    def filtered(reader, messages, **kw):
        return {"text": "", "finish": "content_filter"}
    st = judge45.judge_model(res, P1.LABEL, profiles.load_profile("bench45"), rd, filtered)
    assert st["classed"] == 0 and st["pending_class"] == 2 and st["judge_filtered"] == 2
    assert judge45.JudgeStore(judge45.store_path(res, P1.LABEL)).lines == []   # nothing stored: asked again


def test_the_run_summary_and_the_board_name_the_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)
    j = {"judge_failed": True, "judge_raw": "", "judge_finish_reason": "content_filter", "scores": None}
    (tmp_path / "transcripts_m.jsonl").write_text(json.dumps({"case_id": "C", "judge": j}) + "\n", encoding="utf-8")
    line = cli._judge_filtered_line(["m"])
    assert line.startswith("judge: 1 row(s) not judged (the judge's provider refused or filtered this unit)")
    assert "not scored as the model's refusal" in line
    assert cli._judge_filtered_line(["absent"]) is None


def test_the_session_judge_runs_a_hosted_judge_under_its_own_id(tmp_path, monkeypatch):
    from crucibleforge import rpladder as RL
    from crucibleforge import rpladder_run as LR
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(RL, "load", lambda: {"card": {}})
    monkeypatch.setattr(RL, "flat", lambda cards: ["x"])
    monkeypatch.setattr(RL, "load_check", lambda *a, **k: {"checked": [], "cells": {}})
    monkeypatch.setattr(RL, "rung_check", lambda cards, j, cache, judge_model: j([{"key": "k"}]) and {})
    monkeypatch.setattr(RL, "save_check", lambda *a, **k: None)
    jc = SimpleNamespace(model_id="vendor/some-judge", deadline=None,
                         provider=SimpleNamespace(type="openai", name="router"))
    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: jc)
    monkeypatch.setattr(LR, "make_judge_fn", lambda jc, stats: (lambda reqs: [{} for _ in reqs]))
    out = LR.run({"models": []}, {"judge": {"model_id": QWEN}}, [], windows=[1], rung_check_only=True)
    assert out["other_judge"] == "vendor/some-judge"


# ================================================================== B. judge money apart
def _ledger(monkeypatch, tmp_path):
    led = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    spend.reset()
    return led


def _result(pt=1000, ct=1000):
    return SimpleNamespace(prompt_tokens=pt, completion_tokens=ct, billed_extra_prompt_tokens=0,
                           billed_extra_completion_tokens=0)


def test_judge_calls_are_booked_apart_and_never_meet_the_models_caps(monkeypatch, tmp_path):
    led = _ledger(monkeypatch, tmp_path)
    monkeypatch.setenv(spend.CAP_ENV, "0.0001")                 # the model's cumulative cap: tiny
    monkeypatch.setenv(spend.RUN_CAP_ENV, "0.0001")             # and --spend-cap-usd: tiny
    spend.mark_judge("router~judge")
    price = {"input": 1.0, "output": 1.0}
    with spend.judge_role("router~judge"):
        tok = spend.reserve("openai", "J", 0.01)                # not refused
        spend.guard("openai", "J", own=tok)
        spend.record("openai", "J", price, _result(), estimate=0.01)
        spend.release(tok)
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    assert any(d.get("kind") == "judge" and d.get("cost_usd") == pytest.approx(0.002) for d in lines)
    assert spend.spent() == 0.0 and spend.total() == 0.0       # the model-cost sums skip it
    assert spend.judge_totals()[1] == pytest.approx(0.002)
    with pytest.raises(spend.SpendCapExceeded):                 # a model call still meets its own cap
        spend.reserve("openai", "M", 0.01)


def test_the_global_ceiling_still_counts_the_judge(monkeypatch, tmp_path):
    _ledger(monkeypatch, tmp_path)
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "0.001")
    spend.mark_judge("router~judge")
    with spend.judge_role("router~judge"), pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.reserve("openai", "J", 0.01)


def _two_routes(monkeypatch, prof, judge_price=None, model_price=None):
    """cfg with model M on "router" and the SAME model id named as the hosted judge (served via router~judge)."""
    cfg = _cfg()
    cfg["models"] = [{"name": "M", "provider": "router", "model_id": "vendor/M",
                      **({"price": model_price} if model_price else {})}]
    cfg["judge"]["candidates"].append({"name": "m-judge", "provider": "router", "model_id": "vendor/M",
                                       **({"price": judge_price} if judge_price else {})})
    providers._CACHE.clear()
    args = Namespace(judge="m-judge")
    cfg = cli._guard_45_judge(args, cfg, prof)
    monkeypatch.setattr("crucibleforge.api.stream_chat_retried", lambda *a, **k: _result())
    return cfg, args, providers.get_provider(cfg, "router"), providers.get_provider(cfg, "router~judge")


def test_the_same_model_under_test_and_as_judge_is_booked_by_role(monkeypatch, tmp_path, prof):
    # the reviewer's reproduction (F3): M at its $1 cap is refused; naming M as the judge must not lift that
    led = _ledger(monkeypatch, tmp_path)
    monkeypatch.setenv(spend.CAP_ENV, "1")
    price = {"input": 1.0, "output": 1.0}
    cfg, args, model_p, judge_p = _two_routes(monkeypatch, prof, judge_price=price, model_price=price)
    spend._add("vendor/M", 1.0)                                   # M has spent its cap generating
    with pytest.raises(spend.SpendCapExceeded):
        model_p.chat("vendor/M", [{"role": "user", "content": "x"}])         # generation: still capped
    judge_p.chat("vendor/M", [{"role": "user", "content": "x"}])             # judging: booked apart
    assert spend.judge_totals() == (1, pytest.approx(0.002))
    assert spend.spent() == pytest.approx(1.0)                    # the model's own total did not move
    kinds = [json.loads(x).get("kind") for x in led.read_text(encoding="utf-8").splitlines() if '"cost_usd"' in x]
    assert kinds == [None, "judge"]


def test_an_unpriced_hosted_judge_is_counted_loudly_and_never_charged(monkeypatch, tmp_path, prof):
    _ledger(monkeypatch, tmp_path)
    monkeypatch.setenv(spend.CAP_ENV, "5")                      # an explicit cap would refuse an unpriced MODEL
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", False)     # another test in the worker may have set it
    cfg, args, model_p, judge_p = _two_routes(monkeypatch, prof)
    judge_p.chat("vendor/M", [{"role": "user", "content": "x"}])
    judge_p.chat("vendor/M", [{"role": "user", "content": "x"}])
    assert spend.judge_totals() == (2, 0.0) and spend.judge_tokens() == (2000, 2000)
    assert cli._judge_cost_line(args) == ("judge cost unknown: vendor/M on router has no price in models.yaml; "
                                          "2 calls, 4000 tokens; not limited by any cap")
    with pytest.raises(spend.SpendCapExceeded, match="no price"):
        model_p.chat("vendor/M", [{"role": "user", "content": "x"}])         # the MODEL stays fail-closed


def test_a_priced_hosted_judge_meets_the_overall_ceiling(monkeypatch, tmp_path, prof):
    _ledger(monkeypatch, tmp_path)
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "0.01")       # the second call's reservation would cross it
    cfg, args, model_p, judge_p = _two_routes(monkeypatch, prof, judge_price={"input": 1.0, "output": 1.0})
    judge_p.chat("vendor/M", [{"role": "user", "content": "x"}])             # $0.002 booked
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        judge_p.chat("vendor/M", [{"role": "user", "content": "x"}])


def test_the_run_summary_prints_one_judge_cost_line():
    spend._judge_calls[0], spend._judge_spent[0] = 12, 0.034
    a = Namespace(_hosted_judge={"model_id": "J", "provider": "router", "priced": True})
    assert cli._judge_cost_line(a) == ("judge cost: $0.0340 for 12 call(s) to J on router "
                                       "(billed apart: not part of any model's Run cost)")
    a._hosted_judge["priced"] = False
    assert cli._judge_cost_line(a).startswith("judge cost unknown: J on router has no price in models.yaml; 12 calls")
    assert cli._judge_cost_line(Namespace(_hosted_judge=None)) is None


def test_the_judge_plan_line():
    line = judge45.judge_cost_plan_line({"input": 1.0, "output": 2.0}, 10, 30, 1000, 500, 1000, route="J on r")
    # the upper figure: 30 calls at the measured length (1000 in x $1 + 500 out x $2 per M), the cut priced apart
    assert line.startswith("  judge cost (estimate) (J on r): ~$0.02 expected for ~10 call(s), up to $0.06")
    assert "1,000-token output limit adds up to $0.0010 each" in line
    assert "not capped by --spend-cap-usd" in line
    assert "a lifetime ceiling applies only when CF_SPEND_GLOBAL_CAP_USD is set" in line
    assert judge45.judge_cost_plan_line(None, 10, 30, 1000, 500, 1000, route="J on r") == (
        "  judge cost unknown: J on r has no price in models.yaml; ~10-30 calls, ~0.01M tokens; "
        "not limited by any cap")


def test_the_judge_help_says_how_it_is_billed():
    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    text = (p.format_help() if p else "") + open(cli.__file__, encoding="utf-8").read()
    assert "billed by its provider" in text and "not capped by the run's spend cap" in text


# ================================================================== C. the plan price
def test_the_measured_table_is_dated_and_shaped():
    m = plan_price.MEASURED
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", m["date"]) and m["runs"] >= 1 and m["ref_rows"] > 0
    for k in ("typical", "long_thinker"):
        i, o = m[k]
        assert i > o > 0                                         # input-heavy: chat history is resent
    assert m["long_thinker"][1] > m["typical"][1]
    lo, hi = m["packs_factor"]
    assert 1 < lo <= hi


def _rows(n):
    return [({"id": f"x{i}", "prompt": "a" * 300, "category": "coding"}, 1, 1000) for i in range(n)]


def test_the_estimate_scales_with_rows_and_price():
    m = plan_price.MEASURED
    full = plan_price.estimate(_rows(m["ref_rows"]), {"input": 1.0, "output": 1.0}, thinks=None)
    assert full["in_tok"] == pytest.approx(m["typical"][0] * 1e6)
    assert full["expected_usd"] == pytest.approx(sum(m["typical"]))
    assert full["long_usd"] == pytest.approx(sum(m["long_thinker"]))
    half = plan_price.estimate(_rows(m["ref_rows"] // 2 or 1), {"input": 2.0, "output": 2.0}, thinks=None)
    assert half["expected_usd"] == pytest.approx(2 * full["expected_usd"] * (m["ref_rows"] // 2) / m["ref_rows"])
    assert plan_price.estimate(_rows(3), None, thinks=False)["expected_usd"] is None


def test_the_plan_line_keeps_its_machine_readable_shape():
    est = plan_price.estimate(_rows(279), {"input": 1.0, "output": 4.0}, thinks=None)
    line = plan_price.plan_line(est, hosted=True, priced=True)
    m = re.search(r"price \(estimate[^)]*\): ~\$([\d.]+) expected .*?\$([\d.]+) for a model that thinks at "
                  r"length, \$([\d.]+) if every row ran to its budget", line)
    assert m, line
    x, h, y = (float(g) for g in m.groups())
    assert 0 < x <= h <= y
    assert "packs are extra" in line and "cached input usually bills lower" in line


def test_a_cached_input_price_prices_cache_read_tokens():
    e = {"price": {"input": 2.0, "output": 8.0, "cached_input": 0.2}}
    assert providers.cost_usd(e, 1_000_000, 0, cache_read_tokens=800_000, native_cache=False) == \
        pytest.approx(0.2 * 2.0 + 0.8 * 0.2)
    plain = {"price": {"input": 2.0, "output": 8.0}}                     # no key: list price, as before
    assert providers.cost_usd(plain, 1_000_000, 0, cache_read_tokens=800_000, native_cache=False) == \
        pytest.approx(2.0)


# ================================================================== the user's board shows another judge's NSFW+
def _np_block(res, rows):
    from crucibleforge import nsfwplus45
    return nsfwplus45.report_block(rows, _p1().LABEL, res, profiles.load_profile("bench45"))


def _numbers(blk):
    return json.dumps({k: blk.get(k) for k in ("numbers", "total", "counts", "state")}, sort_keys=True, default=str)


def _judge(res, model_id, provider, ptype, fake):
    return judge45.judge_model(res, _p1().LABEL, profiles.load_profile("bench45"),
                               judge45.judge_reader(model_id, provider=provider, provider_type=ptype), fake)


def test_a_profile_judged_folder_renders_identically(tmp_path, monkeypatch):
    P1 = _p1()
    rows = P1.std_rows()
    res = P1._folder(tmp_path / "f", rows)
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    _judge(res, QWEN, "studioforge", "studioforge", P1.FakeJudge(cls="SOFTENED"))
    before = _np_block(res, rows)                                       # nothing recorded (every older folder)
    assert before["judge45"]["class_missing"] == 0 and before["judge45"]["class_judged"] > 0
    assert "other_judge" not in before
    judge45.record_run_judge(res, QWEN, "studioforge", labels=[P1.LABEL])   # a profile-judge run records itself
    # another judge's votes in the store (an aborted run that never became current) change nothing
    _judge(res, "vendor/some-judge", "router", "openai", P1.FakeJudge(cls="EXPLICIT", quote=lambda r: r[:40]))
    after = _np_block(res, rows)
    assert _numbers(after) == _numbers(before) and "other_judge" not in after
    assert report._np_other_judge_of(rows, {"model_label": P1.LABEL}, None) is None   # never held off public


def test_a_hosted_judge_only_folder_shows_its_nsfw_plus_with_the_note(tmp_path, monkeypatch):
    P1 = _p1()
    rows = P1.std_rows()
    ref = P1._folder(tmp_path / "ref", rows)
    _judge(ref, QWEN, "studioforge", "studioforge", P1.FakeJudge(cls="SOFTENED"))
    res = P1._folder(tmp_path / "f", rows)
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    assert _np_block(res, rows)["judge45"]["class_judged"] == 0          # nothing recorded: not judged yet
    _judge(res, "vendor/some-judge", "router", "openai", P1.FakeJudge(cls="SOFTENED"))
    judge45.record_run_judge(res, "vendor/some-judge", "router", labels=[P1.LABEL])
    judge45.record_judge_provider(res, "vendor/some-judge", "router")
    blk = _np_block(res, rows)
    assert blk["judge45"]["class_missing"] == 0 and blk["other_judge"] == "vendor/some-judge"
    assert _numbers(blk) == _numbers(_np_block(ref, rows))              # the same votes, the same numbers
    s = {"nsfw_plus": blk}
    want = ("judged by vendor/some-judge (router); MakoBench's published scores use the local "
            "Qwen 3.8 27B Q8 judge")
    assert report.np_other_judge(s) == want
    assert report.np_cell(s, "nsfw").endswith(" (judged by vendor/some-judge (router))")
    assert want in report.np_judge_line(s)
    assert f"NSFW+ {want}" in report._model_notes(P1.LABEL, s)
    held = report._np_other_judge_of(rows, {"model_label": P1.LABEL}, None)
    assert held == want                                                # held off a public board, like Chat


def test_two_other_judges_never_mix_the_latest_is_used(tmp_path, monkeypatch):
    P1 = _p1()
    rows = P1.std_rows()
    only_b = P1._folder(tmp_path / "b", rows)
    _judge(only_b, "vendor/judge-b", "router", "openai", P1.FakeJudge(cls="SOFTENED"))
    judge45.record_run_judge(only_b, "vendor/judge-b", "router", labels=[P1.LABEL])
    res = P1._folder(tmp_path / "f", rows)
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    judge45.record_run_judge(res, "vendor/judge-a", "router", labels=[P1.LABEL])
    _judge(res, "vendor/judge-a", "router", "openai", P1.FakeJudge(cls="REFUSED"))
    judge45.record_run_judge(res, "vendor/judge-b", "router", labels=[P1.LABEL])
    _judge(res, "vendor/judge-b", "router", "openai", P1.FakeJudge(cls="SOFTENED"))
    blk = _np_block(res, rows)
    assert blk["other_judge"] == "vendor/judge-b"
    assert _numbers(blk) == _numbers(_np_block(only_b, rows))             # judge-a's votes count for nothing
    assert blk["judge45"]["other_judges_ignored"] == ["vendor/judge-a"]
    assert "votes of vendor/judge-a left out" in report.np_other_judge({"nsfw_plus": blk})


def test_a_no_nsfw_plus_folder_still_reads_not_run(tmp_path, monkeypatch):
    P1 = _p1()
    res = P1._folder(tmp_path / "f", [])
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    judge45.record_run_judge(res, "vendor/some-judge", "router")
    blk = _np_block(res, [])
    assert blk["state"] == "not run"
    assert "judged by" not in report.np_total_cell({"nsfw_plus": blk})


def test_judge_check_shows_the_run_judges_result_when_the_profile_judge_has_none(tmp_path, monkeypatch):
    P1 = _p1()
    rows = P1.std_rows(variants=("write",), n=1)
    res = P1._folder(tmp_path / "f", rows)
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    judge45.run_check(res, judge45.judge_reader("vendor/some-judge", provider="router", provider_type="openai"),
                      P1.FakeJudge())
    assert _np_block(res, rows)["judge_check"] is None                   # not the folder's judge: not shown
    judge45.record_run_judge(res, "vendor/some-judge", "router")
    chk = _np_block(res, rows)["judge_check"]
    assert chk and chk["judge"] == "vendor/some-judge"
    line = report.np_judge_check_line({"m": {"nsfw_plus": {"judge_check": chk}}}, ["m"])
    assert "judged by vendor/some-judge" in line


def test_a_thinking_model_is_expected_at_the_long_thinker_figure():
    m = plan_price.MEASURED
    t = plan_price.estimate(_rows(m["ref_rows"]), {"input": 1.0, "output": 1.0}, thinks=True)
    assert t["expected_usd"] == pytest.approx(sum(m["long_thinker"])) == pytest.approx(t["long_usd"])
    line = plan_price.plan_line(t, hosted=True, priced=True)
    assert "twice the expected figure" in line
    unknown = plan_price.plan_line(plan_price.estimate(_rows(10), {"input": 1.0, "output": 1.0}, thinks=None),
                                   hosted=True, priced=True)
    assert unknown.endswith("Set --spend-cap-usd to about twice the 'thinks at length' figure")
    off = plan_price.estimate(_rows(m["ref_rows"]), {"input": 1.0, "output": 1.0}, thinks=False)
    assert off["expected_usd"] == pytest.approx(sum(m["typical"]))


@pytest.mark.parametrize("price", [(0.15, 0.50), (0.40, 1.60)])
def test_the_long_thinker_figure_with_packs_covers_the_measured_heavy_run(price):
    # GLM-5.3 Flash, full run WITH many packs: 7.99M in / 3.76M out (measured 2026-11-24)
    m = plan_price.MEASURED
    pin, pout = price
    est = plan_price.estimate(_rows(m["ref_rows"]), {"input": pin, "output": pout}, thinks=True)
    measured = 7.99 * pin + 3.76 * pout
    assert est["long_usd"] * m["packs_factor"][1] >= measured


def test_a_later_other_judge_is_the_models_one_judge_and_the_profile_votes_are_kept(tmp_path, monkeypatch):
    # review F2: `judge --judge <other>` after a profile-judged run: one judge per model, the current one
    P1 = _p1()
    rows = P1.std_rows()
    only_h = P1._folder(tmp_path / "h", rows)
    _judge(only_h, "vendor/some-judge", "router", "openai", P1.FakeJudge(cls="REFUSED"))
    judge45.record_run_judge(only_h, "vendor/some-judge", "router", labels=[P1.LABEL])
    res = P1._folder(tmp_path / "f", rows)
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    judge45.record_run_judge(res, QWEN, "studioforge", labels=[P1.LABEL])
    _judge(res, QWEN, "studioforge", "studioforge", P1.FakeJudge(cls="SOFTENED"))
    profile_view = _numbers(_np_block(res, rows))
    judge45.record_run_judge(res, "vendor/some-judge", "router", labels=[P1.LABEL])
    _judge(res, "vendor/some-judge", "router", "openai", P1.FakeJudge(cls="REFUSED"))
    assert _numbers(_np_block(res, rows)) == _numbers(_np_block(only_h, rows))    # the current judge alone
    assert "rows of this model were judged by " + QWEN + ", vendor/some-judge" in report.judges_note(P1.LABEL)
    judge45.record_run_judge(res, QWEN, "studioforge", labels=[P1.LABEL])          # back to the profile judge
    assert _numbers(_np_block(res, rows)) == profile_view                          # its votes were kept


def _row(case, judge_id, run="r1", score=5):
    return {"bench_run_id": run, "case_id": case, "repeat": 1, "turn": None, "needs_judge": True,
            "judge": {"scores": {"s": score}}, "judge_id": judge_id}


def test_standard_rows_use_one_judge_per_model_across_a_forced_rejudge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    lines = [_row("A", QWEN, score=7), _row("B", QWEN, score=6), _row("C", "legacy/older-judge", score=4),
             _row("A", "vendor/some-judge", score=2)]                # `judge --force --judge <other>` on A only
    (tmp_path / "transcripts_m.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    plain = config.load_transcripts("m")                              # nothing recorded: as before
    assert [(r["case_id"], r.get("judge_id")) for r in plain] == [("A", "vendor/some-judge"), ("B", QWEN),
                                                                  ("C", "legacy/older-judge")]
    judge45.record_run_judge(tmp_path, QWEN, "studioforge", labels=["m"])
    judge45.record_run_judge(tmp_path, "vendor/some-judge", "router", labels=["m"])
    cur = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert cur["A"]["judge_id"] == "vendor/some-judge"
    assert "judge" not in cur["B"] and cur["B"]["judge_set_aside"]   # the profile's verdict reads pending now
    assert cur["C"]["judge_id"] == "legacy/older-judge"               # a judge never recorded here: untouched
    judge45.record_run_judge(tmp_path, QWEN, "studioforge", labels=["m"])
    back = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert back["A"]["judge_id"] == QWEN and back["A"]["judge"]["scores"]["s"] == 7   # both kept in the file
    assert back["B"]["judge_id"] == QWEN


def test_other_judge_ids_find_rows_sessions_and_nsfw_plus(monkeypatch):
    rows = [_row("A", QWEN), _row("B", "vendor/some-judge"), {"case_id": "C"}]
    assert report.other_judge_ids("m", None, rows) == ["vendor/some-judge"]
    assert report.other_judge_ids("m", None, [_row("A", QWEN)]) == []


def test_report_public_refuses_another_judge_unless_allowed(monkeypatch, capsys):
    import crucibleforge.report as R
    monkeypatch.setattr(R, "doc_placeholders", lambda: [])
    monkeypatch.setattr(cli, "disclosure_problems", lambda cfg, labels: {})
    monkeypatch.setattr(R, "other_judge_ids", lambda label, cfg, rows=None: ["vendor/some-judge"] if label == "m" else [])
    seen = []
    monkeypatch.setattr(R, "generate", lambda *a, **k: seen.append(k.get("cfg")) or "")
    monkeypatch.setattr(R, "scorecard_section", lambda md: "")
    args = Namespace(public=True, models="m,n", profile=None, check_stale=False, staged_claude_code=False,
                     allow_placeholders=False, out=None, allow_other_judge=False)
    assert cli._cmd_report(args, {"models": []}) == 6 and not seen
    err = capsys.readouterr().err
    assert "refused: report --public" in err and "m: vendor/some-judge" in err and "--allow-other-judge" in err
    args.allow_other_judge = True
    assert cli._cmd_report(args, {"models": []}) == 0 and seen[0]["_allow_other_judge"] is True


# ------------------------------------------------------------------ a hosted judge's answers parse (first hosted run)
OR_URL = "https://openrouter.ai/api/v1"
VERDICT = json.dumps({"prose": 7, "character": 7, "dialogue": 6, "atmosphere": 7, "emotion": 6, "agency": 8,
                      "refused": False, "note": "fine"})
ROW = {"case_id": "C", "rubric": "rp_single", "needs_judge": True, "prompt": "p", "response": "A scene.",
       "finish_reason": "stop", "tool_calls": None, "model_label": "m"}


def _scripted(monkeypatch, answers):
    """Fake transport: each call pops the next (text, finish) and records what was sent."""
    from crucibleforge.api import ChatResult
    sent = []

    def fake(base_url, api_key, model, messages, **kw):
        sent.append({"base_url": base_url, "extra_body": dict(kw.get("extra_body") or {}),
                     "max_tokens": kw.get("max_tokens")})
        text, fin = answers.pop(0)
        return ChatResult(response_text=text, finish_reason=fin, prompt_tokens=100, completion_tokens=50)
    monkeypatch.setattr("crucibleforge.api.stream_chat_retried", fake)
    return sent


def _judge_on(prof, base_url, ptype="openai", **cand):
    cfg = _cfg(orx={"type": ptype, "base_url": base_url, "concurrency": 2})
    cfg["judge"]["candidates"].append({"name": "orj", "provider": "orx", "model_id": "qwen/qwen3.8-27b", **cand})
    providers._CACHE.clear()
    args = Namespace(judge="orj")
    cfg = cli._guard_45_judge(args, cfg, prof)
    spec = args.judge if isinstance(args.judge, dict) else judge.resolve_judge_spec(cfg, "orj")
    return args, judge.JudgeClient(cfg, spec)


def test_an_openrouter_judge_runs_with_openrouters_own_thinking_off_switch(monkeypatch, prof, capsys):
    args, jc = _judge_on(prof, OR_URL)
    assert "thinking off (reasoning.enabled false)" in capsys.readouterr().out
    sent = _scripted(monkeypatch, [(VERDICT, "stop")])
    v = judge._judge_row(jc, dict(ROW))
    assert v["judge_failed"] is False and len(sent) == 1
    assert sent[0]["extra_body"]["reasoning"] == {"enabled": False}
    # the judge's own no-think switch (chat_template_kwargs) is translated on OpenRouter; a user's choice is kept
    assert providers.judge_thinking_off(OR_URL, judge._NO_THINK)["reasoning"] == {"enabled": False}
    keep = {"reasoning": {"effort": "low"}, **judge._NO_THINK}
    assert providers.judge_thinking_off(OR_URL, keep)["reasoning"] == {"effort": "low"}
    assert "reasoning" not in providers.judge_thinking_off("https://api.example.com/v1", judge._NO_THINK)


def test_a_thinking_openrouter_judge_keeps_its_thinking_until_its_no_think_retry(monkeypatch, prof):
    args, jc = _judge_on(prof, OR_URL, thinking=True)
    sent = _scripted(monkeypatch, [("", "stop"), (VERDICT, "stop")])
    v = judge._judge_row(jc, dict(ROW))
    assert v["judge_failed"] is False and len(sent) == 2
    assert "reasoning" not in sent[0]["extra_body"]                       # thinking on, as the entry asks
    assert sent[1]["extra_body"]["reasoning"] == {"enabled": False}        # the no-think retry, said OpenRouter's way


def test_a_hosted_judge_cut_at_its_ceiling_is_asked_once_more_with_four_times_the_budget(monkeypatch, prof):
    args, jc = _judge_on(prof, OR_URL)
    sent = _scripted(monkeypatch, [('{"prose": 7, "char', "length"), (VERDICT, "stop")])
    v = judge._judge_row(jc, dict(ROW))
    assert v["judge_failed"] is False and len(sent) == 2
    assert sent[1]["max_tokens"] == min(sent[0]["max_tokens"] * 4, providers.JUDGE_LENGTH_RETRY_CAP)
    assert spend.judge_totals()[0] == 2                                   # both calls booked as judge calls


def test_the_length_retry_rule():
    R = SimpleNamespace
    assert providers.judge_length_retry_tokens(R(finish_reason="length", response_text="{\"a\""), 8192) == 32768
    assert providers.judge_length_retry_tokens(R(finish_reason="length", response_text=""), 16384) == 32768
    assert providers.judge_length_retry_tokens(R(finish_reason="length", response_text=""), 8192, cap=12000) == 12000
    assert providers.judge_length_retry_tokens(R(finish_reason="length", response_text=""), 40000) is None
    assert providers.judge_length_retry_tokens(R(finish_reason="length", response_text=VERDICT), 8192) is None
    assert providers.judge_length_retry_tokens(R(finish_reason="stop", response_text=""), 8192) is None


def test_a_hosted_judge_whose_first_answer_parses_makes_one_call(monkeypatch, prof):
    args, jc = _judge_on(prof, OR_URL)
    sent = _scripted(monkeypatch, [(VERDICT, "stop")])
    assert judge._judge_row(jc, dict(ROW))["judge_failed"] is False and len(sent) == 1


@pytest.mark.parametrize("answers,calls", [([(VERDICT, "stop")], 1),
                                           # cut: the judge's own retries as before, never a 4x budget
                                           ([("{\"prose\"", "length"), (VERDICT, "stop")], 2)])
def test_a_local_judge_is_sent_exactly_as_before(monkeypatch, prof, answers, calls):
    cfg = _cfg()
    providers._CACHE.clear()
    jc = judge.JudgeClient(cfg, {"provider": "lm", "model_id": "local/judge"})
    sent = _scripted(monkeypatch, list(answers))
    v = judge._judge_row(jc, dict(ROW))
    assert v["judge_failed"] is False and len(sent) == calls
    assert all("reasoning" not in s["extra_body"] for s in sent)
    if calls == 2:
        assert sent[1]["max_tokens"] <= sent[0]["max_tokens"]             # the judge's own half-budget retry
    assert spend.judge_totals()[0] == 0


# ------------------------------------------------------------------ a judge-side "prompt flagged" is the judge's
FLAGGED = ('server error: {"code": 400, "message": "Invalid prompt: your prompt was flagged as potentially violating '
           'our usage policy."}')


def test_a_flagged_prompt_on_a_judge_call_is_never_retried_and_leaves_the_row_not_judged(monkeypatch, prof):
    from crucibleforge import api
    tries = []

    def stream(*a, **k):
        tries.append(1)
        raise api.classify_server_error(None, FLAGGED)
    monkeypatch.setattr(api, "stream_chat", stream)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    args, jc = _judge_on(prof, OR_URL)
    with pytest.raises(api.ProviderFiltered) as e:
        jc.chat([{"role": "user", "content": "x"}])
    assert len(tries) == 1                                                 # one call, no transport retry loop
    assert isinstance(e.value, api.RequestRejected)                         # the judge records it, no reload
    verdict = {"judge_failed": True, "judge_error": str(e.value)[:300], "judge_raw": "", "scores": None}
    assert judge45.judge_filtered(verdict)                                 # "the judge's provider refused or filtered"
    assert not judge.is_balance_error(e.value)


def test_a_flagged_prompt_on_a_models_own_call_keeps_the_transport_path():
    from crucibleforge import api
    e = api.classify_server_error(None, FLAGGED)                         # no judge role on this thread
    assert type(e) is api.TransportError
    spend.mark_judge("x~judge")
    with spend.judge_role("x~judge"):
        assert isinstance(api.classify_server_error(None, FLAGGED), api.ProviderFiltered)
        assert type(api.classify_server_error(None, '{"code": 500, "message": "flagged"}')) is api.TransportError


def test_a_judge_side_refusal_sets_its_own_exit_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text(json.dumps(
        {"case_id": "C", "judge": {"judge_failed": True, "judge_error": "HTTP 400: " + FLAGGED, "judge_raw": ""}})
        + "\n", encoding="utf-8")
    line = cli._judge_filtered_line(["m"])
    assert "1 row(s) not judged (the judge's provider refused or filtered this unit)" in line
    assert "not scored as the model's refusal" in line
    assert cli.JUDGE_REFUSED_EXIT == 12


# ------------------------------------------------------------------ local servers are local
@pytest.mark.parametrize("url,local", [
    ("http://127.0.0.1:8080/v1", True), ("http://localhost:1234/v1", True), ("http://192.168.200.7:1234/v1", True),  # scrub-ok: made-up private address, range test
    ("http://10.0.0.5/v1", True), ("http://100.64.0.1:1234/v1", True), ("http://100.127.255.254/v1", True),  # scrub-ok: made-up private address, range test
    ("http://100.128.0.1/v1", False), ("http://gpubox:1234/v1", True), ("http://rig.local:1234/v1", True),
    ("http://[::1]:8080/v1", True), ("https://openrouter.ai/api/v1", False), ("https://api.example.com/v1", False),
    ("http://8.8.8.8/v1", False), ("", False)])
def test_a_local_server_is_local(url, local):
    assert spend.local_host(url) is local
    assert spend.is_hosted_at("openai", url) is (not local)
    assert spend.is_hosted_at("studioforge", url) is False


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://192.168.200.8/v1",  # scrub-ok: made-up private address
                                 "http://10.1.2.3/v1", "http://100.100.1.1/v1"])  # scrub-ok: made-up private address
def test_an_openai_type_judge_on_a_local_server_is_a_local_judge(monkeypatch, prof, url, capsys):
    cfg = _cfg(lan={"type": "openai", "base_url": url})
    cfg["judge"]["candidates"].append({"name": "lan-judge", "provider": "lan", "model_id": "my/judge"})
    assert judge45.guard_judge(cfg, "lan-judge", prof) is None             # local: no hosted handling
    assert any(c.get("name") == "lan-judge" for c in judge45.local_only_cfg(cfg, prof)["judge"]["candidates"])
    args = Namespace(judge="lan-judge")
    cli._guard_45_judge(args, cfg, prof)
    assert args._hosted_judge is None and cli._judge_cost_line(args) is None
    assert "not limited by any cap" not in capsys.readouterr().out
    rd = judge45.judge_reader("my/judge", provider="lan", provider_type="openai", base_url=url)
    assert rd.hosted is False
    providers._CACHE.clear()
    p = providers.get_provider(cfg, "lan")
    _scripted(monkeypatch, [(VERDICT, "stop")])
    p.chat("my/judge", [{"role": "user", "content": "x"}])
    assert spend.judge_totals()[0] == 0


def test_an_openai_type_judge_on_a_public_host_stays_hosted(prof):
    cfg = _cfg(pub={"type": "openai", "base_url": "https://api.example.com/v1"})
    cfg["judge"]["candidates"].append({"name": "pub-judge", "provider": "pub", "model_id": "my/judge"})
    assert judge45.guard_judge(cfg, "pub-judge", prof)["model_id"] == "my/judge"
    assert judge45.judge_reader("my/judge", provider_type="openai", base_url="https://api.example.com/v1").hosted


def test_the_ladder_names_another_judge_with_the_note_wording():
    import inspect
    from crucibleforge import rpladder_run
    src = inspect.getsource(rpladder_run)
    assert "marks them not comparable" not in src and "not the board's judge" not in src
    flat = re.sub(r'"\s+"', "", src)
    assert "judged by %s; MakoBench's published scores use the local %s judge" in flat


# ------------------------------------------------------------------ second review notes 4-6 (2026-11-25)

def test_a_tilde_in_a_user_provider_name_is_refused_at_load():
    cfg = {"providers": {"x~judge": {"type": "openai", "base_url": "https://api.example.invalid/v1"}},
           "models": [], "judge": {"candidates": []}, "defaults": {}}
    with pytest.raises(config.ConfigError, match="'~' is reserved"):
        config.validate_config(cfg)


def test_the_plan_judge_range_allows_four_calls_per_sample():
    assert cli.HOSTED_JUDGE_CALLS_PER_SAMPLE == 4
    line = judge45.judge_cost_plan_line({"input": 1.0, "output": 2.0}, 10, 120, 1000, 500, 1000, route="J on r")
    assert "(~120 calls: every call asked again under the retry rules, each at the measured reply length;" in line
    src = open(cli.__file__, encoding="utf-8").read()
    # review D (2026-11-25): the plan counts judged rows in judge_call_model, whose upper figure is rows x 4
    assert '"high": rows * HOSTED_JUDGE_CALLS_PER_SAMPLE' in src
    assert 'out["calls_high"] * HOSTED_JUDGE_CALLS_PER_SAMPLE' in src


def test_the_judge_and_model_status_say_hosted_or_local():
    cfg = {"providers": {"or": {"type": "openai", "base_url": "https://openrouter.ai/api/v1"},
                         "gw": {"type": "openai", "base_url": "http://192.168.200.7:4000/v1"},  # scrub-ok: made-up test address
                         "gw2": {"type": "openai", "base_url": "http://192.168.200.8:4000/v1", "hosted": True},  # scrub-ok: made-up test address
                         "rig": {"type": "studioforge", "base_url": "http://192.168.200.9:1234/v1"}},  # scrub-ok: made-up test address
           "judge": {"candidates": [{"name": "loc", "provider": "rig", "model_id": "q"}]}}
    assert cli._where(cfg, "or") == "hosted (or)"
    assert cli._where(cfg, "or~judge") == "hosted (or)"
    assert cli._where(cfg, "rig") == "local (rig)"
    assert cli._where(cfg, "gw2") == "hosted (gw2)"
    assert cli._where(cfg, "gw").startswith("local (gw: an API type at a local address; a paid API behind it? "
                                            "set hosted: true on providers.gw)")
    hj = Namespace(_hosted_judge={"model_id": "J", "provider": "or"}, judge=None)
    assert cli._judge_status(hj, cfg) == "judge: J hosted (or)"
    assert cli._judge_status(Namespace(judge="loc"), cfg) == "judge: q local (rig)"
    assert cli._judge_status(Namespace(judge=None), cfg) is None
    assert cli._model_status_lines(cfg, [{"name": "m", "provider": "gw2"}]) == ["  m: hosted (gw2)"]


def test_the_run_cost_footnote_names_a_hosted_judge(monkeypatch):
    """Docker user path 2026-11-25: the footnote always said "the judge runs locally", even with a hosted judge."""
    from crucibleforge import judge45 as J
    cfg = {"providers": {"or": {"type": "openai", "base_url": "https://openrouter.ai/api/v1"},
                         "rig": {"type": "openai", "base_url": "http://192.168.200.7:1234/v1"}}}  # scrub-ok: made-up test address
    prof = {"judge": {"model_id": "local/judge", "provider": "rig"}}
    monkeypatch.setattr(J, "judge_providers", lambda results=None: {"x/hosted-judge": "or"})
    stats = {"m": {"other_judge45": {"ids": ["x/hosted-judge"]}}, "n": {}}
    assert "the judge runs locally" in report.cost_footnote(prof, ["n"], stats, cfg)
    t = report.cost_footnote(prof, ["m", "n"], stats, cfg)
    assert "the judge runs locally" not in t
    assert "the judge is billed apart and not counted here: x/hosted-judge on or (hosted)" in t


def test_the_typical_figure_is_not_under_the_measured_light_runs_on_input():
    """Docker user path 2026-11-25: GPT-6 Luna planned $0.47 and booked $0.74 (4.39M in / 0.58M out at $0.10 /
    $0.50). The re-measured profiles: typical within 30% of that run, thinkers' input at their measured median."""
    m = plan_price.MEASURED
    est = plan_price.estimate(_rows(m["ref_rows"]), {"input": 0.10, "output": 0.50}, thinks=None)
    booked = 4.39 * 0.10 + 0.58 * 0.50
    assert booked * 0.7 <= est["expected_usd"] <= booked * 1.1
    assert est["long_usd"] >= booked
    assert m["long_thinker"][0] >= 7.77                   # every measured thinker run but the two lightest
