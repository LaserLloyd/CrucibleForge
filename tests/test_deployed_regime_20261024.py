"""Suite 3.8.0 — the "as deployed" regime (2026-10-24 bench review).

The review found the board was not measuring typical use: every coding and
steer case ran at temperature 0 (vendors advise against greedy decoding for
thinking models; nothing on this box runs a model that way), hosted APIs got a
65k thinking budget where rig models got 24k, and a thinking run that ran out
of budget was silently re-asked with thinking OFF and graded at full credit
(71% of rig thinkers' Programs rows). 3.8.0 measures a model the way it is
deployed, stamps every row with that regime, and keeps the old board visible —
marked, with its suite — until each model is re-run.
"""
from __future__ import annotations

import json
from datetime import datetime

from crucibleforge import api, bench_all, config, report, runner, version
from crucibleforge.profiles import apply_profile, load_profile
from tests.test_api import _patch_capture, _sse
from tests.test_root_causes_20260822 import _FakeProvider, _answer, _overflow
import pytest

# suite 3.9: these tests are written against the 3.8 case set and test the
# board/escalation mechanism, not the profile — run them on the frozen 3.8
# profile (conftest.bench_38)
pytestmark = pytest.mark.usefixtures("bench_38")


DEPLOYED = {"regime": "deployed", "thinking_max_tokens_factor": 4,
            "thinking_max_tokens_cap": 24576, "thinking_max_tokens_floor": 16384,
            "reasoning_overflow_recovery": False}


def _dctx(provider, entry=None, cfg_extra=None, **defaults):
    cfg = {"defaults": {**DEPLOYED, **defaults}, **(cfg_extra or {})}
    e = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True, **(entry or {})}
    return runner._Ctx(cfg, e, provider, 32768)


# ------------------------------------------------------------ request body

def _ok():
    return _sse({"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
                {"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def test_no_sampler_is_sent_when_none(monkeypatch):
    captured = {}
    _patch_capture(monkeypatch, captured, _ok())
    api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}],
                    max_tokens=8, temperature=None, top_p=None)
    assert "temperature" not in captured["json"] and "top_p" not in captured["json"]


def test_an_explicit_sampler_is_still_sent(monkeypatch):
    captured = {}
    _patch_capture(monkeypatch, captured, _ok())
    api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}],
                    max_tokens=8, temperature=0.0, top_p=1.0)
    assert captured["json"]["temperature"] == 0.0 and captured["json"]["top_p"] == 1.0


# ------------------------------------------------------------------ runner

def test_deployed_sends_the_models_default_sampler():
    ctx = _dctx(_FakeProvider([]))
    assert ctx.sampling({"temperature": 0.0, "top_p": 1.0}) == (None, None)


def test_a_registry_entry_may_pin_its_production_sampler():
    ctx = _dctx(_FakeProvider([]), entry={"sampler": {"temperature": 0.3}})
    assert ctx.sampling({"temperature": 0.0}) == (0.3, None)
    assert ctx.regime_stamp()["sampler"] == {"temperature": 0.3, "source": "registry"}


def test_the_old_regime_keeps_the_case_sampler():
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m"}, _FakeProvider([]), 4096)
    assert ctx.sampling({"temperature": 0.0, "top_p": 1.0}) == (0.0, 1.0)
    assert ctx.sampling({}) == (0.0, 1.0)


def test_every_provider_gets_the_same_thinking_budget():
    prov = _FakeProvider([])     # type "openai": a hosted API
    cfg_extra = {"providers": {"fake": {"thinking_max_tokens_cap": 65536,
                                        "thinking_max_tokens_factor": 12}}}
    ctx = _dctx(prov, cfg_extra=cfg_extra)
    assert ctx.budget(6144) == 24576            # not 65536: the profile's cap for everyone
    # the old regime still honoured the per-provider override
    old = runner._Ctx({"defaults": {"thinking_max_tokens_factor": 4,
                                    "thinking_max_tokens_cap": 24576}, **cfg_extra},
                      {"name": "m", "model_id": "m", "thinking": True}, prov, 32768)
    assert old.budget(6144) == 65536


def test_the_floor_keeps_short_chat_cases_from_starving_a_thinker():
    ctx = _dctx(_FakeProvider([]))
    assert ctx.budget(768) == 16384             # steer: 768 x 4 = 3072 before
    assert ctx.budget(2000) == 16384
    ctx.thinking = False
    # suite 3.9 (EVAL-FABLE S8): one budget per row for every model — a
    # non-thinking model gets the same board budget, not the raw case budget
    assert ctx.budget(768) == 16384
    ctx.one_budget = False                      # run_plan.one_budget: false = the old rule
    assert ctx.budget(768) == 768


def test_no_thinking_off_retry_an_overflow_is_the_answer():
    prov = _FakeProvider([_overflow(), _answer("should never be asked")])
    ctx = _dctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=4096)
    assert len(prov.calls) == 1 and r.response_text == "" and r.recovery is None


def test_rows_and_meta_carry_the_regime():
    ctx = _dctx(_FakeProvider([]), entry={"extra_body": {"reasoning_format": "deepseek"}})
    assert ctx.regime_stamp() == {"regime": "deployed", "reasoning_format": "deepseek",
                                  "thinking": True, "sampler": "default"}


def test_the_bench_profile_is_the_deployed_regime():
    cfg, cases = apply_profile(load_profile("bench"), {"defaults": {}})
    d = cfg["defaults"]
    assert d["regime"] == "deployed" and d["reasoning_overflow_recovery"] is False
    assert d["thinking_max_tokens_floor"] == 16384 and d["thinking_max_tokens_cap"] == 24576
    for cat in ("coding", "tooluse", "instruct", "reasoning", "math"):
        assert config.repeats_for(cfg, cat) == 3
    assert config.repeats_for(cfg, "rp") == 1


# ----------------------------------------------------------------- version

def test_old_stamps_are_the_old_board_not_current():
    # suite 3.9.0: the 3.8.0 board is now the old board too (3.10.0: same regime)
    assert version.SUITE_VERSION == "4.1.0"  # key review 2026-11-08: 4.1.0 (per-case currency, nothing stales on the bump)
    cur = version.current_revisions({})
    old = version.previous_regime_revisions()
    assert "3.7.0+36a60ec6" in old and "3.4.0+0d6e0ad1" in old and "3.8.0+36a60ec6" in old
    assert not cur & old
    assert version.suite_of("3.5.0+d2d09bda") == "3.5.0"
    assert version.stamp_version("3.10.0+x") > version.stamp_version("3.8.0+x")


# ------------------------------------------------------------------ report

IZ = "IZ01-logistics-47-words"
TZ = "TZ01-contradicts-user-assumption"


def _row(cid, cat, rev, grade="pass", run="r1", ts="2026-10-01T00:00:00+00:00", **kw):
    return {"bench_run_id": run, "bench_revision": rev, "profile": "bench", "case_id": cid,
            "repeat": 1, "turn": None, "category": cat, "grade": grade, "ts": ts,
            "metrics": {"tok_per_s": 30.0}, **kw}


def _seed(tmp_path, monkeypatch, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "_profile_case_ids", lambda cfg, **k: {IZ, TZ})
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (tmp_path / "meta_m.json").write_text(json.dumps(meta or {
        "model_id": "x/y", "provider": "p", "profile": "bench", "failed": True,
        "error": "stopped by user", "bench_revision": version.revision()}), encoding="utf-8")


def test_old_values_stay_on_the_board_marked_while_the_rerun_is_partial(tmp_path, monkeypatch):
    cur = version.revision()
    _seed(tmp_path, monkeypatch, [
        _row(IZ, "instruct", "3.5.0+d2d09bda", "pass"),
        _row(TZ, "tooluse", "3.4.0+0d6e0ad1", "pass"),
        # tonight's partial 3.8.0 run: one case done, then the window closed
        _row(IZ, "instruct", cur, "fail", run="new", ts="2026-10-25T01:30:00+00:00",
             regime="deployed")])
    rows, era = report.board_view("m", None)
    assert era["old"] and era["rerun_cases"] == 1 and era["expected"] == 2
    assert {r["bench_revision"] for r in rows} == {"3.5.0+d2d09bda", "3.4.0+0d6e0ad1"}
    labels, stats, _ = report.board_stats(None, None)
    s = stats["m"]
    assert report.suite_cell(s) == "3.4.0/3.5.0 (old)"
    cov = s["coverage"]
    assert not cov["failed"]                    # the re-run's "stopped" meta is not the old rows'
    assert any("old values" in n and "re-run 1/2" in n for n in cov["notes"])
    md = report.render_markdown(labels, stats, None)
    assert "| Suite |" in md and "3.4.0/3.5.0 (old)" in md and "still on old values" in md
    h = report.html_rows(labels, stats)[0]
    assert h["old"] is True and h["suite"] == "3.4.0/3.5.0 (old)"


def test_a_complete_current_run_replaces_the_old_values(tmp_path, monkeypatch):
    cur = version.revision()
    _seed(tmp_path, monkeypatch, [
        _row(IZ, "instruct", "3.5.0+d2d09bda", "pass"),
        _row(TZ, "tooluse", "3.5.0+d2d09bda", "pass"),
        _row(IZ, "instruct", cur, "fail", run="new", regime="deployed"),
        _row(TZ, "tooluse", cur, "pass", run="new", regime="deployed")],
        meta={"model_id": "x/y", "provider": "p", "profile": "bench",
              "bench_revision": cur, "finished": "2026-10-25T02:00:00+00:00"})
    rows, era = report.board_view("m", None)
    assert not era["old"] and {r["bench_revision"] for r in rows} == {cur}
    _, stats, _ = report.board_stats(None, None)
    assert report.suite_cell(stats["m"]) == version.SUITE_VERSION


def test_a_model_with_no_old_rows_shows_its_partial_current_run(tmp_path, monkeypatch):
    cur = version.revision()
    _seed(tmp_path, monkeypatch, [_row(IZ, "instruct", cur, "pass", regime="deployed")])
    rows, era = report.board_view("m", None)
    assert not era["old"] and len(rows) == 1


def test_an_unjudged_current_run_is_not_complete(tmp_path, monkeypatch):
    cur = version.revision()
    _seed(tmp_path, monkeypatch, [
        _row(IZ, "instruct", "3.5.0+d2d09bda"), _row(TZ, "tooluse", "3.5.0+d2d09bda"),
        _row(IZ, "instruct", cur, run="new"),
        _row(TZ, "tooluse", cur, run="new", needs_judge=True)])
    _, era = report.board_view("m", None)
    assert era["old"]


def test_an_empty_reply_scores_zero_under_the_deployed_regime():
    empty = {"judge": {"judge_failed": True, "empty_generation": True, "scores": None}}
    new = report._judged([{**empty, "regime": "deployed", "rubric": "story"}])
    assert len(new) == 1 and report._scores(new[0]).get("prose") == 0
    assert not report.steer_obeyed(new[0])
    # rows of the old regime keep their old rule (dropped from the averages)
    assert report._judged([{**empty, "rubric": "story"}]) == []


def test_budget_exhausted_answers_are_counted():
    rows = [_row(IZ, "instruct", version.revision(), "fail", regime="deployed",
                 finish_reason="length", response="", reasoning="thinking...")]
    s = report.model_stats("m", None, rows=rows, meta={})
    assert s["budget_exhausted"] == 1
    assert any("never arrived" in n for n in report._model_notes("m", s))


# --------------------------------------------------------------- bench-all

def test_a_case_needs_every_repeat_before_it_is_done():
    rows = [{"case_id": "CZ02", "repeat": 1, "grade": "pass"},
            {"case_id": "CZ02", "repeat": 2, "grade": "fail"},
            {"case_id": "RPS1", "repeat": 1, "grade": "pass"}]
    assert bench_all.missing_cases(rows, ["CZ02", "RPS1"], repeats={"CZ02": 3, "RPS1": 1}) == ["CZ02"]
    rows.append({"case_id": "CZ02", "repeat": 3, "grade": "pass"})
    assert bench_all.missing_cases(rows, ["CZ02", "RPS1"], repeats={"CZ02": 3, "RPS1": 1}) == []


def test_the_time_window():
    gen, end = bench_all.deadlines("03:00", 45, now=datetime(2026, 10, 25, 1, 0))
    assert end == datetime(2026, 10, 25, 3, 0) and gen == datetime(2026, 10, 25, 2, 15)
    # started after the edge (a late manual run): the next 03:00 is 23 h away —
    # refused since the 3.9 Fable review (44cb439: an --until already past
    # today is a missed day-run end time, MAX_WINDOW_H)
    with pytest.raises(SystemExit):
        bench_all.deadlines("03:00", 45, now=datetime(2026, 10, 25, 4, 0))
    _, end2 = bench_all.deadlines("03:00", 45, now=datetime(2026, 10, 25, 14, 0))
    assert end2 == datetime(2026, 10, 26, 3, 0)
    assert bench_all.deadlines(None, 45) == (None, None)


def test_bench_all_plans_from_current_rows_only(tmp_path, monkeypatch):
    """The old-values view must never make bench-all think a model is done."""
    _seed(tmp_path, monkeypatch, [_row(IZ, "instruct", "3.5.0+d2d09bda"),
                                  _row(TZ, "tooluse", "3.5.0+d2d09bda")])
    assert report.board_filter("m", None) == []
    assert report.board_view("m", None)[1]["old"]


def test_old_regime_errors_do_not_use_up_a_cases_retries(tmp_path, monkeypatch):
    err = {"grade": "error", "error_kind": "transport"}
    _seed(tmp_path, monkeypatch, [
        _row(IZ, "instruct", "3.5.0+d2d09bda", **err),
        _row(IZ, "instruct", "3.5.0+d2d09bda", run="r2", **err),
        _row(TZ, "tooluse", version.revision(), **err)])
    assert bench_all.error_attempts("m") == {TZ: 1}


# ------------------------------------------------ the link-check probe follows the regime

def test_the_link_probe_sends_no_sampler_under_the_deployed_regime():
    """kimi-k3 refuses any temperature but 1: the old fixed temperature=0
    probe aborted its 3.8.0 run before a single case (2026-10-25)."""
    from types import SimpleNamespace
    from crucibleforge import preflight
    sent = []
    prov = SimpleNamespace(chat=lambda mid, msgs, **kw: sent.append(kw))
    dep = {"defaults": {"regime": "deployed"}}
    preflight._probe(prov, "kimi-k3", {}, preflight._probe_sampler(dep, {"name": "k"}))
    assert sent[-1]["temperature"] is None and sent[-1]["top_p"] is None
    preflight._probe(prov, "m", {}, preflight._probe_sampler(dep, {"sampler": {"temperature": 1.0}}))
    assert sent[-1]["temperature"] == 1.0
    preflight._probe(prov, "m", {}, preflight._probe_sampler({"defaults": {}}, {}))
    assert sent[-1]["temperature"] == 0.0


# ------------------------------------------------ the maker's recommended sampler

def test_a_recommended_sampler_rule_applies_and_is_stamped():
    from types import SimpleNamespace
    from crucibleforge import runner
    rules = [{"id": "qwen3-thinking", "match": r"qwen3\.[5-9]", "source": "https://x",
              "sampler": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}},
             {"id": "gemma4", "match": "gemma-4", "sampler": {"temperature": 1.0, "top_p": 0.95, "top_k": 64}}]
    cfg = {"defaults": {"regime": "deployed", "recommended_samplers": rules}}
    prov = SimpleNamespace(name="sf", type="studioforge", model_capabilities=lambda m: set())
    ctx = runner._Ctx(cfg, {"name": "q", "model_id": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q5_K_S"}, prov, 32768)
    assert ctx.sampling({"temperature": 0.0}) == (0.6, 0.95)
    assert ctx.extra_body["top_k"] == 20 and ctx.extra_body["min_p"] == 0.0
    st = ctx.regime_stamp()["sampler"]
    assert st["source"] == "qwen3-thinking" and st["temperature"] == 0.6


def test_the_registry_sampler_wins_and_no_match_sends_nothing():
    from types import SimpleNamespace
    from crucibleforge import runner
    cfg = {"defaults": {"regime": "deployed",
                        "recommended_samplers": [{"id": "any", "match": ".", "sampler": {"temperature": 0.7}}]}}
    prov = SimpleNamespace(name="p", type="openai")
    pinned = runner._Ctx(cfg, {"name": "k", "model_id": "k", "sampler": {"temperature": 1.0}}, prov, 8192)
    assert pinned.sampling({}) == (1.0, None) and pinned.regime_stamp()["sampler"]["source"] == "registry"
    bare = runner._Ctx({"defaults": {"regime": "deployed"}}, {"name": "m", "model_id": "m"}, prov, 8192)
    assert bare.sampling({}) == (None, None) and bare.regime_stamp()["sampler"] == "default"
    assert "top_k" not in bare.extra_body


def test_a_registry_extra_body_value_is_never_overridden_by_a_rule():
    from types import SimpleNamespace
    from crucibleforge import runner
    cfg = {"defaults": {"regime": "deployed",
                        "recommended_samplers": [{"id": "r", "match": ".", "sampler": {"top_k": 20}}]}}
    prov = SimpleNamespace(name="p", type="openai")
    ctx = runner._Ctx(cfg, {"name": "m", "model_id": "m", "extra_body": {"top_k": 5}}, prov, 8192)
    assert ctx.extra_body["top_k"] == 5


def test_the_bench_profile_gives_every_known_family_its_makers_sampler():
    """The rules in profiles/bench.yaml resolve as published (2026-10-25)."""
    import yaml
    from crucibleforge import runner
    from crucibleforge.profiles import PROFILES_DIR
    rules = yaml.safe_load((PROFILES_DIR / "bench.yaml").read_text(encoding="utf-8"))["recommended_samplers"]
    cfg = {"defaults": {"recommended_samplers": rules}}
    def get(prov, mid):
        return runner.resolve_sampler(cfg, {"name": "x", "provider": prov, "model_id": mid})
    assert get("deepseek", "deepseek-v4-pro") == ({}, "api-vendor-fixed")
    assert get("moonshot", "kimi-k3") == ({}, "api-vendor-fixed")
    s, src = get("studioforge", "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q5_K_S")
    assert src == "qwen3-thinking" and s["min_p"] == 0.0 and s["top_k"] == 20
    assert get("studioforge", "ReadyArt/Dark-Scarlett-27B-v3.0-GGUF/x")[0]["top_p"] == 0.92
    assert get("studioforge", "ReadyArt/Swish-31B-Final-GGUF/x")[1] == "readyart-gemma4"
    assert get("studioforge", "unsloth/gemma-4-26B-A4B-it-qat-GGUF/x")[1] == "gemma4"
    assert get("studioforge", "mradermacher/JoyFox-Qwen3.6-35B-A3B-RP-Aggressive-i1-GGUF/x")[1] == "joyfox-thinking"
    assert get("studioforge", "24_10Mistrial_NSFW_Noromaid-7b") == ({}, "default")
