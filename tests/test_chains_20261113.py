"""4.5 chains (crucibleforge/chains.py, EXP-20261113-45): slip-guard chains, paired seeds,
monotone scores, resume, early stop, fail-closed judge rungs. Stub provider only."""
from __future__ import annotations

import copy
import json
import math
import random
import re
import types
from pathlib import Path

import pytest

from crucibleforge import chains, config, earlystop, elemstats, gen, profiles, providers, runner, stopflag
from crucibleforge.api import ChatResult
from crucibleforge.config import ConfigError

LEVELS = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}}


def _toy(name: str, judged: bool = False, turns: bool = False):
    def materialize(g, *, release_seed, case_id):
        p = f"TOY level={g['level']} key={g['key']} case={case_id}"
        if turns:
            return {"system": "s", "turns": [p, "again"], "checks": [], "judge_elements": [],
                    "programmatic": True, "max_tokens": 64, "temperature": 0.0}
        out = {"prompt": p, "max_tokens": 64, "temperature": 0.0, "grader": "contains",
               "grader_config": {"needles": ["PASS"]}}
        if judged:
            out["rubric"] = "rp_multi"
        return out
    return types.SimpleNamespace(
        FAMILY=name, LEVELS=LEVELS, materialize=materialize,
        reference=lambda g, **kw: "PASS", mutants=lambda g, **kw: ["nope"],
        grade=lambda a, m: 1.0 if "PASS" in a else 0.0)


@pytest.fixture(autouse=True)
def _toys():
    for f in (_toy("toychain"), _toy("toychain2"), _toy("toyjudge", judged=True)):
        gen.register(f)
    chains._judge_reason.cache_clear()
    chains._family_source_digest.cache_clear()
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    for f in ("toychain", "toychain2", "toyjudge"):
        gen.unregister(f)
    providers.clear_cache()


def _prof(n=4, slip=1, ladders=None, **sec):
    ladders = ladders or {"toy": [{"family": "toychain", "level": lv} for lv in (1, 2, 3, 4)]}
    return {"name": "t", "cases": {"coding": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/main": {"chains": n, "slip_guard": slip, "ladders": ladders, **sec}}}


# ------------------------------------------------------------------ pure algorithm

def _run_table(K, slip, table):
    return chains.advance(K, slip, table)


def _play(K, slip, policy):
    """Drive advance() with policy(r, a) -> bool; returns (held, attempts)."""
    res = {}
    while True:
        st = chains.advance(K, slip, res)
        if st["next"] is None:
            return st["held"], len(st["path"])
        res[st["next"]] = policy(*st["next"])


def test_all_pass_holds_every_rung():
    assert _play(5, 1, lambda r, a: True) == (5, 5)


def test_all_fail_costs_two_attempts_and_no_deeper_rung():
    assert _play(5, 1, lambda r, a: False) == (0, 2)
    assert _play(5, 0, lambda r, a: False) == (0, 1)


def test_passes_up_to_level_then_fails():
    assert _play(5, 1, lambda r, a: r <= 3) == (3, 5)      # 3 passes + fail + failed retry


def test_slip_once_then_climbs_on():
    assert _play(5, 1, lambda r, a: not (r == 2 and a == 1)) == (5, 6)
    # the guard is spent: a later failure ends the chain at once
    assert _play(5, 1, lambda r, a: not (r == 2 and a == 1) and r != 4) == (3, 5)


@pytest.mark.parametrize("seed", range(4))
def test_failing_on_purpose_never_raises_the_score(seed):
    rnd = random.Random(seed)
    for _ in range(3000):
        K, slip = rnd.randint(1, 6), rnd.randint(0, 2)
        table = {(r, a): rnd.random() < 0.7 for r in range(1, K + 1) for a in range(1, slip + 2)}
        base = _play(K, slip, lambda r, a: table[(r, a)])[0]
        for k in [k for k, v in table.items() if v]:
            worse = {**table, k: False}
            assert _play(K, slip, lambda r, a: worse[(r, a)])[0] <= base, (K, slip, table, k)


def test_rung_states_mark_not_reached():
    st = chains.advance(4, 1, {(1, 1): True, (2, 1): False, (2, 2): False})
    assert chains.rung_states(4, st["path"]) == {1: "held", 2: "failed", 3: "not reached",
                                                 4: "not reached"}


# ------------------------------------------------------------------ profile

def test_profile_syntax_and_round_robin():
    p = _prof(n=5, ladders={"a": [{"family": "toychain", "level": 1}],
                            "b": [{"family": "toychain2", "level": 1}, {"family": "toychain", "level": 3,
                                                                        "pass": 0.75}]})
    cs = chains.chain_cases(p)
    assert [c["id"] for c in cs] == ["CHAIN-a-001", "CHAIN-b-002", "CHAIN-a-003", "CHAIN-b-004",
                                     "CHAIN-a-005"]
    sp = cs[1]["chain_spec"]
    assert sp["K"] == 2 and sp["slip_guard"] == 1 and sp["rungs"][1]["pass"] == 0.75
    assert sp["rungs"][0]["pass"] == 0.5 and sp["rungs"][0]["category"] == "coding"


@pytest.mark.parametrize("bad", [
    {"chains": 0}, {"slip_guard": -1}, {"pass": 0}, {"pass": 1.5},
    {"ladders": {"x": [{"family": "toychain", "level": 9}]}},
    {"ladders": {"x": [{"family": "nosuchfamily", "level": 1}]}},
    {"ladders": {"x": [{"family": "toychain", "level": 1, "budget": 4096}]}},
    {"ladders": {"x": []}}, {"surprise": 1}])
def test_bad_sections_refused(bad):
    p = _prof()
    p["chains"]["work/main"].update(bad)
    with pytest.raises(ConfigError):
        chains.sections(p)


def test_unknown_part_refused():
    p = _prof()
    p["chains"] = {"work/nope": p["chains"]["work/main"]}
    with pytest.raises(ConfigError):
        chains.sections(p)


def test_judge_graded_rung_refused_at_profile_apply():
    p = _prof(ladders={"j": [{"family": "toychain", "level": 1}, {"family": "toyjudge", "level": 2}]})
    chains.sections(p)                      # structurally fine...
    with pytest.raises(ConfigError, match="needs a judge"):
        chains.check_gradeable(p)           # ...but refused before a run
    with pytest.raises(ConfigError, match="needs a judge"):
        profiles.apply_profile(p, {"defaults": {}})


def test_paired_seeds_and_fresh_retry():
    c = chains.chain_cases(_prof())[0]
    a = chains.instance(c, 2, 1, release_seed="R")
    b = chains.instance(c, 2, 1, release_seed="R")
    assert a == b                                            # same for every model
    retry = chains.instance(c, 2, 2, release_seed="R")
    assert retry["generator"]["key"] != a["generator"]["key"]  # never a seen instance
    other = chains.instance(chains.chain_cases(_prof())[1], 2, 1, release_seed="R")
    assert other["generator"]["key"] != a["generator"]["key"]
    assert chains.instance(c, 2, 1, release_seed="S")["generator"]["key"] != a["generator"]["key"]


# ------------------------------------------------------------------ runner (stub provider)

_LEVEL = re.compile(r"level=(\d+) key=(\w+) case=(\S+)")


def _fake_rig(monkeypatch, policies, workers=3, fail_transport=None):
    """policies: model -> f(level, case_id) -> answer text (or a ChatResult)."""
    calls: dict[str, list] = {m: [] for m in policies}

    def chat(self, model_id, messages, **kw):
        text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        m = _LEVEL.search(text)
        if m is not None and getattr(policies[model_id], "full_text", False):
            calls[model_id].append((m.group(3), kw.get("max_tokens")))
            out = policies[model_id](text)
            if isinstance(out, ChatResult):
                return out
            return ChatResult(response_text=out, finish_reason="stop", served_model=model_id,
                              completion_tokens=5, prompt_tokens=20, reasoning_tokens=2, total_s=0.01)
        if m is None:                   # the thinking probe, not a chain instance
            return ChatResult(response_text="395", finish_reason="stop", served_model=model_id,
                              completion_tokens=3)
        lv, cid = int(m.group(1)), m.group(3)
        calls[model_id].append((cid, kw.get("max_tokens")))
        if fail_transport and fail_transport(model_id, lv, cid):
            raise runner.TransportError("boom")
        out = policies[model_id](lv, cid)
        if isinstance(out, ChatResult):
            return out
        return ChatResult(response_text=out, finish_reason="stop", served_model=model_id,
                          completion_tokens=5, prompt_tokens=20, reasoning_tokens=2, total_s=0.01)

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", lambda self, mid, ctx=None: 1.0)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "live_context", lambda self, mid: None)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {"parallel": workers})
    monkeypatch.setattr(P, "workers", lambda self, mid: workers)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return calls


def _cfg(prof, **extra):
    return {"defaults": {"context_length": 32768, "repeats": {"coding": 1},
                         "early_stop": dict(earlystop.DEFAULTS, enabled=False)},
            "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
            "judge": {"candidates": []}, "models": [],
            # the toy runs include adult parts: they run as `--adult` does (opt-in since 2026-11-18)
            "adult_run": True,
            "_profile_case_ids": [c["id"] for c in chains.chain_cases(prof)], **extra}


def _run(tmp_path, monkeypatch, prof, policies, workers=3, fail_transport=None, cfg_extra=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    calls = _fake_rig(monkeypatch, policies, workers, fail_transport)
    cfg = _cfg(prof, **(cfg_extra or {}))
    entries = [{"name": m, "model_id": m, "provider": "p"} for m in policies]
    summary = runner.run_models(cfg, entries, chains.chain_cases(prof), skip_conformance=True)
    return calls, summary


ACE = lambda lv, cid: "PASS"                                     # noqa: E731
ZERO = lambda lv, cid: "nope"                                    # noqa: E731
MID = lambda lv, cid: "PASS" if lv <= 2 else "nope"              # noqa: E731
SLIP = lambda lv, cid: "nope" if cid.endswith("-r1-a1") else "PASS"  # noqa: E731


def test_runner_end_to_end_four_kinds_of_model(tmp_path, monkeypatch):
    prof = _prof(n=6)
    calls, summary = _run(tmp_path, monkeypatch, prof,
                          {"ace": ACE, "zero": ZERO, "mid": MID, "slip": SLIP})
    assert all(not s.get("failed") for s in summary.values()), summary
    got = {m: chains.section_stats(config.load_transcripts(m), prof)["work/main"] for m in calls}
    assert got["ace"]["share"] == 1.0 and got["ace"]["points"] == 10.0 and got["ace"]["turns"] == 24
    assert got["zero"]["share"] == 0.0 and got["zero"]["turns"] == 12   # N x 2, no deeper rung
    assert {c[0].split("-r")[1][0] for c in calls["zero"]} == {"1"}
    assert got["mid"]["share"] == 0.5 and got["mid"]["mean_level_held"] == 2.0
    assert got["mid"]["turns"] == 6 * 4
    assert got["slip"]["share"] == 1.0 and got["slip"]["turns"] == 6 * 5
    for s in got.values():
        assert s["complete"] and s["ended"] == 6 and s["chains"] == 6
    # every attempt is a row with its chain block, verdict and cost fields
    rows = config.load_transcripts("mid")
    r = rows[0]
    for k in ("id", "rung", "attempt", "family", "level", "seed_id", "score", "passed"):
        assert k in r["chain"]
    assert r["chain_revision"] == chains.revision(r["chain"])
    assert r["metrics"]["prompt_tokens"] == 20 and r["metrics"]["reasoning_tokens"] == 2
    per = got["mid"]["per_chain"]["CHAIN-toy-001"]["rungs"]
    assert per == {1: "held", 2: "held", 3: "failed", 4: "not reached"}


def test_first_rungs_of_all_chains_go_first(tmp_path, monkeypatch):
    prof = _prof(n=8)
    calls, _ = _run(tmp_path, monkeypatch, prof, {"ace": ACE}, workers=1)
    first8 = [c[0] for c in calls["ace"][:8]]
    assert all(cid.endswith("-r1-a1") for cid in first8), first8
    assert len({cid.split("-r")[0] for cid in first8}) == 8


def test_paired_instances_identical_across_models(tmp_path, monkeypatch):
    prof = _prof(n=4)
    _run(tmp_path, monkeypatch, prof, {"ace": ACE, "slip": SLIP})
    by = {m: {r["case_id"]: (r["prompt"], r["chain"]["seed_id"]) for r in config.load_transcripts(m)}
          for m in ("ace", "slip")}
    common = set(by["ace"]) & set(by["slip"])
    assert len(common) == 16
    assert all(by["ace"][i] == by["slip"][i] for i in common)


def test_chain_rows_get_the_model_maximum_and_a_cut_is_a_fail(tmp_path, monkeypatch):
    prof = _prof(n=2)
    cut = lambda lv, cid: ChatResult(response_text="PASS", finish_reason="length",  # noqa: E731
                                      served_model="m", completion_tokens=9, prompt_tokens=20)
    calls, _ = _run(tmp_path, monkeypatch, prof, {"cut": cut})
    sent = {mt for _cid, mt in calls["cut"]}
    assert min(sent) > 30000          # the window minus the prompt, not the case's 64 (R23)
    rows = config.load_transcripts("cut")
    assert all(r["chain"]["passed"] is False and r["chain"]["cut_off"] for r in rows)
    assert all(r["chain"]["score"] == 1.0 for r in rows)   # the score is kept for the report
    assert all(earlystop.token_limited(r) for r in rows)


def test_resume_continues_mid_chain(tmp_path, monkeypatch):
    prof = _prof(n=3)
    # first run: the rig drops every rung-3 request (transport): chains halt at rung 3
    calls1, _ = _run(tmp_path, monkeypatch, prof, {"ace": ACE},
                     fail_transport=lambda m, lv, cid: lv == 3)
    st = chains.section_stats(config.load_transcripts("ace"), prof)["work/main"]
    assert st["ended"] == 0 and not st["complete"]
    providers.clear_cache()
    calls2, _ = _run(tmp_path, monkeypatch, prof, {"ace": ACE})
    # the second run starts at rung 3: rungs 1-2 are not presented again
    assert sorted({c[0].split("-r")[1][0] for c in calls2["ace"]}) == ["3", "4"]
    assert len(calls2["ace"]) == 6
    st = chains.section_stats(config.load_transcripts("ace"), prof)["work/main"]
    assert st["complete"] and st["share"] == 1.0 and st["turns"] == 12
    # a third run has nothing left to do
    providers.clear_cache()
    calls3, _ = _run(tmp_path, monkeypatch, prof, {"ace": ACE})
    assert calls3["ace"] == []


def test_early_stop_counts_chain_answers(tmp_path, monkeypatch):
    prof = _prof(n=40)
    loop = lambda lv, cid: ChatResult(response_text="", reasoning_text="mm", finish_reason="length",  # noqa: E731
                                       served_model="m", completion_tokens=9)
    _run(tmp_path, monkeypatch, prof, {"bad": loop, "ace": ACE}, workers=1,
         cfg_extra={"defaults": {"context_length": 32768, "repeats": {"coding": 1},
                                 "reasoning_overflow_recovery": False,
                                 "early_stop": dict(earlystop.DEFAULTS)}})
    bad = json.loads((tmp_path / "meta_bad.json").read_text(encoding="utf-8"))
    assert bad["failed"] is True and bad["early_stop"]["signal"] == "blank"
    kept = config.load_transcripts("bad")
    assert 20 <= len(kept) <= 22 and all(r.get("chain") for r in kept)
    # breadth first: the window sat on first rungs
    assert all(r["chain"]["rung"] == 1 for r in kept)
    ace = json.loads((tmp_path / "meta_ace.json").read_text(encoding="utf-8"))
    assert ace["early_stop"]["checked"] and not ace["early_stop"]["tripped"]
    assert ace["early_stop"]["answers_seen"] == 30


def test_section_stats_standard_error_and_margin():
    prof = _prof(n=4)
    rows = []
    for cid, held in (("CHAIN-toy-001", 4), ("CHAIN-toy-002", 2), ("CHAIN-toy-003", 0),
                      ("CHAIN-toy-004", 2)):
        path = [(r, 1, True) for r in range(1, held + 1)]
        if held < 4:
            path += [(held + 1, 1, False), (held + 1, 2, False)]
        for r, a, ok in path:
            rows.append({"chain": {"id": cid, "rung": r, "attempt": a, "passed": ok},
                         "metrics": {"prompt_tokens": 10}, "cost_usd": 0.001})
    s = chains.section_stats(rows, prof)["work/main"]
    xs = [1.0, 0.5, 0.0, 0.5]
    m = sum(xs) / 4
    sd = (sum((x - m) ** 2 for x in xs) / 3) ** 0.5
    assert s["share"] == pytest.approx(0.5) and s["points"] == pytest.approx(5.0)
    assert s["se_points"] == pytest.approx(10 * sd / 2)
    assert s["margin_points"] == pytest.approx(1.96 * 10 * sd / 2)
    assert s["turns"] == len(rows) and s["cost_usd"] == pytest.approx(0.001 * len(rows))
    assert chains.render_lines({"work/main": s})[2].startswith("| work/main | 5.00 / 10 | 4/4 |")


def test_real_code_graded_families_grade_in_run(tmp_path, monkeypatch):
    """A multi-turn programmatic chat family (session_state multi) and a single-turn checks
    family (ledger) are graded in-run: every attempt gets a verdict, nothing waits for a judge."""
    prof = _prof(n=2, ladders={"ss": [{"family": "session_state", "variant": "multi", "level": 3},
                                      {"family": "session_state", "variant": "multi", "level": 6}],
                               "lg": [{"family": "ledger", "level": 1}, {"family": "ledger", "level": 2}]})
    prof["scores"]["work"]["parts"]["main"]["scoring"] = ["rpdeep"]
    prof["chains"]["work/main"]["ladders"]["lg"][0]["category"] = "coding"
    prof["chains"]["work/main"]["ladders"]["lg"][1]["category"] = "coding"
    chains.check_gradeable(prof)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _fake_rig(monkeypatch, {"m": None})
    monkeypatch.setattr(providers.Provider, "chat", lambda self, mid, msgs, **kw: ChatResult(
        response_text="I am not sure.", finish_reason="stop", served_model=mid, completion_tokens=4))
    runner.run_models(_cfg(prof), [{"name": "m", "model_id": "m", "provider": "p"}],
                      chains.chain_cases(prof), skip_conformance=True)
    rows = config.load_transcripts("m")
    finals = [r for r in rows if r["chain"].get("passed") is not None]
    assert {r["chain"]["family"] for r in finals} == {"session_state", "ledger"}
    assert all(r["chain"]["score"] is not None for r in finals)
    s = chains.section_stats(rows, prof)["work/main"]
    assert s["complete"] and s["share"] == 0.0 and s["turns"] == 4
    # the session rows carry one row per scripted turn; only the last holds the verdict
    ss = [r for r in rows if r["chain"]["family"] == "session_state"]
    assert len(ss) > len([r for r in ss if r["chain"].get("passed") is not None])


def test_bench_all_sees_halted_chains_as_missing(tmp_path, monkeypatch):
    from crucibleforge import bench_all
    prof = _prof(n=3)
    _run(tmp_path, monkeypatch, prof, {"ace": ACE},
         fail_transport=lambda m, lv, cid: lv == 3 and cid.startswith("CHAIN-toy-002"))
    ids = [c["id"] for c in chains.chain_cases(prof)]
    rows = config.load_transcripts("ace")
    assert bench_all.missing_cases(rows, ids) == ["CHAIN-toy-002"]
    assert chains.ended_from_rows(rows) == chains.completed(prof, rows) == {"CHAIN-toy-001",
                                                                            "CHAIN-toy-003"}


def test_board_points_and_partial_from_chain_rows():
    from crucibleforge import report
    prof = _prof(n=4)
    rows = []
    for cid, held in (("CHAIN-toy-001", 4), ("CHAIN-toy-002", 2), ("CHAIN-toy-003", 0),
                      ("CHAIN-toy-004", 2)):
        path = [(r, 1, True) for r in range(1, held + 1)]
        path += [(held + 1, 1, False), (held + 1, 2, False)] if held < 4 else []
        rows += [{"chain": {"id": cid, "rung": r, "attempt": a, "passed": ok, "K": 4, "slip_guard": 1},
                  "category": "coding", "case_id": f"{cid}-r{r}-a{a}", "repeat": 1,
                  "grade": "pass" if ok else "fail"} for r, a, ok in path]
    s = report.stats_from_tier_rows("m", None, prof, {1: rows, 2: [], 3: []}, None, None)
    assert not ({"CHAIN-toy-001", "CHAIN-toy-004"} & set(s["missing_cases"]))
    card = report.points_scorecard(s, {}, prof)
    assert card["parts"]["work"]["main"] == pytest.approx(5.0) and card["total"] == pytest.approx(5.0)
    ch = card["ladder"]["work"]["main"]["chains"]
    assert ch["ended"] == 4 and ch["margin_points"] > 0 and ch["turns"] == len(rows)
    # the last chain not ended yet: the part is partial, the total is withheld
    s = report.stats_from_tier_rows("m", None, prof, {1: rows[:-2], 2: [], 3: []}, None, None)
    card = report.points_scorecard(s, {}, prof)
    assert card["partial"] == ["work"] and card["total"] is None
    assert "partial" in card["labels"]["work"]
    # the board's Chains block: one row per (model, section) with its measured margin
    s["scorecard"] = card
    md = "\n".join(report._points_tables(["m"], {"m": s}, {}, None, prof))
    assert "### Generated items" in md and "| m | work/main | " in md and "3/4" in md


# ------------------------------------------------------------------ start rung hook, ceiling, ran out

def test_start_rung_credits_lower_rungs():
    st = chains.advance(5, 1, {(3, 1): True, (4, 1): False, (4, 2): False}, start=3)
    assert st["ended"] and st["held"] == 3 and len(st["path"]) == 3
    assert chains.rung_states(5, st["path"], 3) == {1: "credited", 2: "credited", 3: "held",
                                                    4: "failed", 5: "not reached"}
    # failing the start rung keeps the credited rungs (held = start - 1)
    assert chains.advance(5, 0, {(3, 1): False}, start=3)["held"] == 2
    # default unchanged
    assert chains.advance(5, 0, {})["next"] == (1, 1)


def test_start_rung_keeps_seeds_paired():
    c = chains.chain_cases(_prof())[0]
    assert (chains.instance(c, 3, 1, release_seed="R", start=1)["generator"]
            == chains.instance(c, 3, 1, release_seed="R", start=3)["generator"])


def test_start_policy_hook_in_a_run(tmp_path, monkeypatch):
    """A plugged-in policy: the first 2 chains calibrate from rung 1; later chains start one
    rung below the best level held so far (decided only once a calibration chain ended)."""
    def adaptive(spec, ended, params):
        if spec["c"] <= params["calibrate"]:
            return 1
        if not ended:
            return None                     # not decidable yet: wait for a calibration chain
        return max(1, max(e["held"] for e in ended.values()) - 1)
    chains.register_start_policy("test_adaptive", adaptive)
    try:
        prof = _prof(n=5, start={"policy": "test_adaptive", "calibrate": 2})
        calls, _ = _run(tmp_path, monkeypatch, prof, {"mid": MID}, workers=1)
    finally:
        chains.START_POLICIES.pop("test_adaptive")
    rows = config.load_transcripts("mid")
    starts = chains.starts_from_rows(rows)
    assert starts["CHAIN-toy-001"] == starts["CHAIN-toy-002"] == 1
    assert {starts[f"CHAIN-toy-00{i}"] for i in (3, 4, 5)} == {1}   # MID holds 2 -> start 1
    # rung 2 presented first for the later chains would need held 3: check a stronger model
    s = chains.section_stats(rows, prof)["work/main"]
    assert s["complete"] and s["share"] == 0.5


def test_start_rung_recorded_and_credited_on_the_board(tmp_path, monkeypatch):
    chains.register_start_policy("test_three", lambda spec, ended, p: 3)
    try:
        prof = _prof(n=2, start={"policy": "test_three"})
        calls, _ = _run(tmp_path, monkeypatch, prof, {"mid": MID, "ace": ACE})
    finally:
        chains.START_POLICIES.pop("test_three")
    assert {c[0].split("-r")[1][0] for c in calls["ace"]} == {"3", "4"}    # rungs 1-2 not presented
    s = chains.section_stats(config.load_transcripts("mid"), prof)["work/main"]
    # MID fails rung 3: held = 2 (credited), score 0.5, 1 attempt each (slip 1 -> 2 attempts)
    assert s["share"] == 0.5 and s["turns"] == 4
    assert s["per_chain"]["CHAIN-toy-001"]["rungs"] == {1: "credited", 2: "credited",
                                                        3: "failed", 4: "not reached"}
    ace = chains.section_stats(config.load_transcripts("ace"), prof)["work/main"]
    assert ace["share"] == 1.0 and ace["turns"] == 4
    # resume keeps the recorded start (the policy is gone now: "first" would say 1)
    assert chains.starts_from_rows(config.load_transcripts("ace"))["CHAIN-toy-002"] == 3


def test_ceiling_from_profile_and_ran_out_outcome(tmp_path, monkeypatch):
    prof = _prof(n=2, ceiling={"default": 4096, "toychain": 2500})
    out = lambda lv, cid: ChatResult(response_text="", reasoning_text="think", finish_reason="length",  # noqa: E731
                                      served_model="m", completion_tokens=2500, prompt_tokens=20)
    calls, _ = _run(tmp_path, monkeypatch, prof, {"out": out})
    assert {mt for _c, mt in calls["out"]} == {2500}
    rows = config.load_transcripts("out")
    assert all(r["chain"]["outcome"] == "ran out" and r["chain"]["passed"] is False
               and r["chain"]["score"] == 0.0 for r in rows)
    s = chains.section_stats(rows, prof)["work/main"]
    assert s["complete"] and s["outcomes"] == {"ran out": 4} and s["share"] == 0.0


def test_ceiling_raises_the_load_floor():
    prof = _prof(n=1, ceiling=65536)
    ph = chains.chain_cases(prof)[0]
    assert runner.fixed_budget({"defaults": {}}, ph) == 65536
    assert runner.wide_context({"defaults": {}}, [ph]) == 65536 + runner.ESCALATION_CTX_PAD


# ------------------------------------------------------------------ flat sets, mixed parts, defaults

def test_slip_guard_defaults_to_zero():
    p = _prof()
    del p["chains"]["work/main"]["slip_guard"]
    assert chains.sections(p)[0]["slip_guard"] == 0


def _mixed_prof():
    p = _prof(n=2, ladders={"toy": [{"family": "toychain", "level": 1}, {"family": "toychain", "level": 3}]})
    p["chains"]["work/main"]["flat"] = {"fl": {"family": "toychain2", "level": 2, "count": 3}}
    p["scores"]["work"]["parts"]["solve"] = {"max": 5, "scoring": ["coding"], "cases": []}
    p["chains"]["work/solve"] = {"flat": {"fs": {"family": "toychain", "level": 4, "count": 4,
                                                 "ceiling": 32768}}}
    return p


def test_flat_units_ids_seeds_and_profile():
    p = _mixed_prof()
    ids = [c["id"] for c in chains.chain_cases(p)]
    assert ids == ["CHAIN-toy-001", "CHAIN-toy-002", "FLAT-fl-001", "FLAT-fl-002", "FLAT-fl-003",
                   "FLAT-fs-001", "FLAT-fs-002", "FLAT-fs-003", "FLAT-fs-004"]
    f = chains.chain_cases(p)[5]
    inst = chains.instance(f, 1, 1, release_seed="R")
    assert inst["id"] == "FLAT-fs-001-r1-a1" and inst["chain"]["kind"] == "flat"
    assert inst["chain"]["ceiling"] == 32768 and inst == chains.instance(f, 1, 1, release_seed="R")
    with pytest.raises(ConfigError):          # names key the seeds: unique across ladders and sets
        bad = _mixed_prof()
        bad["chains"]["work/solve"]["flat"] = {"toy": {"family": "toychain", "level": 1, "count": 1}}
        chains.sections(bad)


def test_mixed_part_and_score_level_margin(tmp_path, monkeypatch):
    p = _mixed_prof()
    # passes levels <= 2 everywhere: chains hold 1 of 2 (0.5); flat L2 pass (1.0); flat L4 fail (0)
    calls, _ = _run(tmp_path, monkeypatch, p, {"mid": MID})
    st = chains.section_stats(config.load_transcripts("mid"), p)
    main, solve = st["work/main"], st["work/solve"]
    assert main["units"] == 5 and main["chains"] == 2 and main["flat"] == 3 and main["complete"]
    assert main["share"] == pytest.approx((0.5 + 0.5 + 1 + 1 + 1) / 5)
    assert main["mean_level_held"] == 1.0
    assert solve["share"] == 0.0 and solve["turns"] == 4 and solve["outcomes"] == {"wrong": 4}
    assert {mt for cid, mt in calls["mid"] if cid.startswith("FLAT-fs")} == {32768}
    comb = chains.score_stats(st)["work"]
    assert comb["points"] == pytest.approx(main["points"] + solve["points"])
    assert comb["se_points"] == pytest.approx((main["se_points"] ** 2 + solve["se_points"] ** 2) ** 0.5)
    assert comb["margin_points"] == pytest.approx(1.96 * comb["se_points"])


def test_flat_partial_credit_is_the_unit_score():
    p = _prof(n=1)
    p["chains"]["work/main"] = {"flat": {"fl": {"family": "toychain", "level": 1, "count": 2}}}
    rows = [{"chain": {"id": "FLAT-fl-001", "kind": "flat", "rung": 1, "attempt": 1, "K": 1,
                       "slip_guard": 0, "score": 0.4, "passed": False, "outcome": "wrong"}},
            {"chain": {"id": "FLAT-fl-002", "kind": "flat", "rung": 1, "attempt": 1, "K": 1,
                       "slip_guard": 0, "score": 0.9, "passed": True, "outcome": "pass"}}]
    s = chains.section_stats(rows, p)["work/main"]
    assert s["share"] == pytest.approx(0.65) and s["complete"] and s["points"] == pytest.approx(6.5)


# ------------------------------------------------------------------ rc1-night: flat elements

def _toy_turns(name: str, n_turns: int = 4):
    """A multi-turn programmatic toy: turn t passes when its reply contains PASS; every turn
    but the last has a single-turn check (the last has none: not a turn element); one check
    spans turns 1-2 (no turn element owns it)."""
    def materialize(g, *, release_seed, case_id):
        p = f"TOY level={g['level']} key={g['key']} case={case_id}"
        checks = [{"id": f"t{t}", "turns": [t], "scope": "all", "weight": 1, "type": "require_regex",
                   "pattern": "PASS"} for t in range(1, n_turns)]
        checks.append({"id": "span", "turns": [1, 2], "scope": "all", "weight": 1,
                       "type": "require_regex", "pattern": "PASS"})
        return {"system": "s", "turns": [f"{p} t={t}" for t in range(1, n_turns + 1)],
                "checks": checks, "judge_elements": [], "programmatic": True, "max_tokens": 64,
                "temperature": 0.0, "category": "rpdeep", "history": [], "user_name": "u"}
    return types.SimpleNamespace(
        FAMILY=name, LEVELS=LEVELS, materialize=materialize,
        reference=lambda g, **kw: "PASS", mutants=lambda g, **kw: ["nope"],
        grade=lambda a, m: 1.0 if "PASS" in a else 0.0)


@pytest.fixture
def _turntoy():
    gen.register(_toy_turns("toyturns"))
    yield
    gen.unregister("toyturns")


def _flatprof(items, **sec):
    return {"name": "t", "cases": {"coding": [], "chat": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}},
                       "chat": {"parts": {"main": {"max": 10, "scoring": ["chat"], "cases": []}}}},
            "chains": {"work/main": {"flat": items, **sec}}}


def test_flat_levels_map_and_list_form():
    p = _flatprof([{"family": "toychain", "levels": {2: 3, 4: 2}, "ceiling": 4000},
                   {"family": "toychain2", "level": 1, "count": 2, "name": "other"}])
    secs = chains.sections(p)[0]["flat"]
    assert [(f["name"], f["count"], f["item"]["level"]) for f in secs] == \
        [("toychain_L2", 3, 2), ("toychain_L4", 2, 4), ("other", 2, 1)]
    ids = [c["id"] for c in chains.chain_cases(p)]
    assert ids == ["FLAT-toychain_L2-001", "FLAT-toychain_L2-002", "FLAT-toychain_L2-003",
                   "FLAT-toychain_L4-001", "FLAT-toychain_L4-002", "FLAT-other-001", "FLAT-other-002"]
    # dict form still works and names stay unique across sets
    with pytest.raises(ConfigError):
        chains.sections(_flatprof([{"family": "toychain", "levels": {2: 1}},
                                   {"family": "toychain", "level": 3, "count": 1, "name": "toychain_L2"}]))
    with pytest.raises(ConfigError):
        chains.sections(_flatprof([{"family": "toychain", "levels": {2: 1}, "level": 2}]))


def test_multi_item_units_pack_the_single_item_instances():
    single = _flatprof([{"family": "toychain", "level": 2, "count": 7}])
    multi = _flatprof([{"family": "toychain", "level": 2, "count": 7, "items_per_prompt": 5,
                        "ceiling": 20000}])
    cs, cm = chains.chain_cases(single), chains.chain_cases(multi)
    assert [c["id"] for c in cm] == ["FLAT-toychain-001", "FLAT-toychain-002"]
    assert cm[0]["chain_spec"]["items"] == [1, 2, 3, 4, 5] and cm[1]["chain_spec"]["items"] == [6, 7]
    keys_single = [chains.instance(c, 1, 1, release_seed="R")["generator"]["key"] for c in cs]
    keys_multi = [k for c in cm for k in chains.instance(c, 1, 1, release_seed="R")["chain"]["items"]]
    assert keys_single == keys_multi                       # packing never changes the instances
    inst = chains.instance(cm[0], 1, 1, release_seed="R")
    assert inst["chain"]["ceiling"] == 65536               # min(5 x 20000, 65,536)
    assert chains.instance(cm[1], 1, 1, release_seed="R")["chain"]["ceiling"] == 65536
    case = chains.materialize(inst)
    assert case["prompt"].count("=== TASK") == 5 and "=== ITEM k ===" in case["prompt"]
    assert [s["id"] for s in case["multi_items"]] == [f"FLAT-toychain-001-i{k}" for k in range(1, 6)]
    small = _flatprof([{"family": "toychain", "level": 2, "count": 4, "items_per_prompt": 2,
                        "ceiling": 3000}])
    assert chains.instance(chains.chain_cases(small)[0], 1, 1)["chain"]["ceiling"] == 6000


def test_split_items():
    a = "=== ITEM 2 ===\nB\n\n**=== ITEM 1 ===**\nA\n=== ITEM 3 ===\n\n=== ITEM 9 ===\nZ"
    assert chains.split_items(a, 3) == ["A", "B", None]


_ITEMS = re.compile(r"case=(\S+)")


def _multi_policy(fn):
    """A stub model for multi-item prompts: fn(sub-item case id) -> answer or None (skip)."""
    def pol(text):
        ids = _ITEMS.findall(text)
        out = []
        for k, cid in enumerate(ids, 1):
            ans = fn(cid)
            if ans is not None:
                out.append(f"=== ITEM {k} ===\n{ans}\n")
        return "".join(out)
    pol.full_text = True
    return pol


def test_multi_item_prompt_grades_each_sub_item(tmp_path, monkeypatch):
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 10, "items_per_prompt": 5}])
    # item k passes when k is odd; item 4 of each prompt is left out (missing)
    def fn(cid):
        k = int(cid.rsplit("-i", 1)[1])
        return None if k == 4 else ("PASS" if k % 2 else "nope")
    calls, summary = _run(tmp_path, monkeypatch, prof, {"m": _multi_policy(fn)})
    assert not summary["m"].get("failed"), summary
    rows = sorted(config.load_transcripts("m"), key=lambda r: r["case_id"])
    assert len(rows) == 2 and len(calls["m"]) == 2           # two prompts, one request each
    els = rows[0]["chain"]["elements"]
    assert [e["id"] for e in els] == [f"FLAT-toychain-001-i{k}" for k in range(1, 6)]
    assert [e["score"] for e in els] == [1.0, 0.0, 1.0, 0.0, 1.0]
    assert els[3]["outcome"] == "missing" and "segment" in els[3]["reason"]
    assert els[1]["outcome"] == "wrong" and els[0]["outcome"] == "pass"
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["elements"] == 10 and st["ended"] == 2 and st["units"] == 2
    assert st["share"] == pytest.approx(0.6) and st["points"] == pytest.approx(6.0)
    assert st["outcomes"] == {"pass": 6, "wrong": 2, "missing": 2}
    assert st["tokens_per_element"] == pytest.approx((20 + 2 + 5) * 2 / 10)


def test_multi_item_prompt_that_runs_out(tmp_path, monkeypatch):
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 5, "items_per_prompt": 5,
                       "ceiling": 2000}])

    def pol(text):
        ids = _ITEMS.findall(text)
        body = "".join(f"=== ITEM {k} ===\nPASS\n" for k in (1, 2, 3))
        return ChatResult(response_text=body, finish_reason="length", served_model="m",
                          completion_tokens=9, prompt_tokens=20)
    pol.full_text = True
    calls, _ = _run(tmp_path, monkeypatch, prof, {"m": pol})
    assert {mt for _c, mt in calls["m"]} == {10000}         # min(5 x 2000, 65,536) per prompt
    r = config.load_transcripts("m")[0]
    els = r["chain"]["elements"]
    assert [e["score"] for e in els] == [1.0, 1.0, 1.0, 0.0, 0.0]
    assert [e["outcome"] for e in els] == ["pass", "pass", "ran out", "ran out", "ran out"]
    assert r["chain"]["outcome"] == "ran out" and r["chain"]["passed"] is False
    assert earlystop.token_limited(r)


def test_multi_item_refused_for_multi_turn_items(_turntoy):
    p = _flatprof([{"family": "toyturns", "level": 2, "count": 5, "items_per_prompt": 5}])
    with pytest.raises(ConfigError, match="single-turn"):
        chains.check_gradeable(p)


def test_per_turn_elements(tmp_path, monkeypatch, _turntoy):
    prof = _flatprof([{"family": "toyturns", "level": 2, "count": 3, "element": "turn",
                       "ceiling_per_turn": 2500}])
    chains.check_gradeable(prof)
    turn = re.compile(r"t=(\d+)")

    def pol(text):
        t = int(turn.search(text).group(1))
        if t == 2:
            return ChatResult(response_text="PASS but cut", finish_reason="length", served_model="m",
                              completion_tokens=9, prompt_tokens=20)
        return "PASS" if t != 3 else "nope"
    pol.full_text = True
    calls, _ = _run(tmp_path, monkeypatch, prof, {"m": pol})
    assert {mt for _c, mt in calls["m"]} == {2500}           # the per-turn ceiling on every turn
    rows = config.load_transcripts("m")
    last = [r for r in rows if r["chain"].get("elements")]
    assert len(last) == 3
    els = last[0]["chain"]["elements"]
    assert [e["id"].rsplit("-", 1)[1] for e in els] == ["t1", "t2", "t3"]   # turn 4 has no checks
    assert [e["score"] for e in els] == [1.0, 1.0, 0.0]
    assert [e["outcome"] for e in els] == ["pass", "ran out", "wrong"]
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["elements"] == 9 and st["share"] == pytest.approx(2 / 3)
    assert st["outcomes"] == {"pass": 3, "ran out": 3, "wrong": 3}


def test_per_turn_refused_for_single_turn_family():
    with pytest.raises(ConfigError, match="multi-turn"):
        chains.check_gradeable(_flatprof([{"family": "toychain", "level": 1, "count": 2,
                                           "element": "turn"}]))
    with pytest.raises(ConfigError):
        chains.sections(_flatprof([{"family": "toychain", "level": 1, "count": 2,
                                    "element": "turn", "items_per_prompt": 2}]))


def test_icc_and_cluster_se():
    assert chains.icc_anova([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]]) == pytest.approx(1.0)
    rnd = random.Random(3)
    g = [[rnd.random() for _ in range(5)] for _ in range(200)]
    assert abs(chains.icc_anova(g)) < 0.05
    cl = [[1, 1], [0, 0], [1, 1], [0, 0]]
    e, p = chains.mean_se(cl, "element"), chains.mean_se(cl, "prompt")
    assert p > e                       # perfectly correlated pairs: the prompt is the unit
    assert chains.mean_se([[1], [0], [1], [0]], "prompt") == pytest.approx(
        chains.mean_se([[1], [0], [1], [0]], "element") * math.sqrt(3 / 4 * 4 / 3), rel=0.2)


def _rows_for(unit, els, kind_score=None):
    return {"case_id": unit + "-r1-a1", "ts": "1",
            "chain": {"id": unit, "rung": 1, "attempt": 1, "passed": True, "score": 1.0,
                      "elements": [{"id": f"{unit}-i{k}", "score": v, "outcome": "pass" if v else "wrong"}
                                   for k, v in enumerate(els, 1)]},
            "metrics": {}}


def test_section_se_unit_auto_switches_on_icc():
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 20, "items_per_prompt": 5}])
    units = [c["id"] for c in chains.chain_cases(prof)]
    # within-prompt independent: element unit
    indep = [_rows_for(u, v) for u, v in zip(units, [[1, 0, 1, 0, 1], [0, 1, 0, 1, 0],
                                                     [1, 1, 0, 0, 1], [0, 0, 1, 1, 0]])]
    st = chains.section_stats(indep, prof)["work/main"]
    assert st["se_unit"] == "element" and st["icc"] < 0.1 and st["elements"] == 20
    # whole prompts right or wrong: prompt unit, wider margin
    corr = [_rows_for(u, v) for u, v in zip(units, [[1] * 5, [0] * 5, [1] * 5, [0] * 5])]
    sc = chains.section_stats(corr, prof)["work/main"]
    assert sc["se_unit"] == "prompt" and sc["icc"] > 0.9
    assert sc["margin_points"] > st["margin_points"]
    forced = dict(prof, chains={"work/main": {**prof["chains"]["work/main"], "se_unit": "element"}})
    assert chains.section_stats(corr, forced)["work/main"]["se_unit"] == "element"
    lines = chains.render_board(["m"], {"m": {"work/main": sc}}, prof)
    assert any("prompt (" in ln for ln in lines)


def test_overall_combines_scores_by_points():
    s = {"work/a": {"points": 6.0, "max": 10, "se_points": 1.0, "complete": True},
         "chat/b": {"points": 4.0, "max": 10, "se_points": 2.0, "complete": True}}
    o = chains.overall_stats(s, {"headline": {"overall": "sum"}})
    assert o["points"] == 10.0 and o["se_points"] == pytest.approx(math.sqrt(5))
    m = chains.overall_stats(s, {"headline": {"overall": "mean"}, "scores": {"work": {}, "chat": {}}})
    assert m["points"] == 5.0 and m["se_points"] == pytest.approx(math.sqrt(5) / 2)


def test_rc1_night_profile_shape_parses():
    """The COMPOSE.md rc1-night sketch, as chains.flat lists (toy families stand in)."""
    p = _flatprof([{"family": "toychain", "levels": {1: 10, 3: 10}, "items_per_prompt": 5, "ceiling": 32768},
                   {"family": "toychain2", "levels": {3: 10, 5: 10}, "items_per_prompt": 5, "ceiling": 42000}])
    p["chains"]["chat/main"] = {"flat": [{"family": "toychain", "level": 2, "count": 3, "name": "ss",
                                          "ceiling_per_turn": 2500}]}
    cs = chains.chain_cases(p)
    assert len([c for c in cs if c["chain_spec"]["part"] == "work/main"]) == 8
    assert {c["chain_spec"]["rungs"][0]["ceiling"] for c in cs if c["chain_spec"]["part"] == "chat/main"} == {2500}


def test_rc1_night_real_families_reference_answers_score_full(tmp_path, monkeypatch):
    """The rc1-night element kinds on the REAL families, answered by a stub that replies with
    each family's own reference: multi-item tracevm / keyfunc / istack-write, per-turn
    attend, whole-item session_state multi_hard. Every element must score 1.0 in-run; a
    stub that answers nothing must score 0 everywhere with nothing missing from the board."""
    from crucibleforge.gen._stack_common import split_turns
    prof = {"name": "t", "cases": {"coding": [], "rpdeep": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}},
                       "chat": {"parts": {"main": {"max": 10, "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {
                "work/main": {"flat": [
                    {"family": "tracevm", "levels": {6: 2}, "items_per_prompt": 2, "ceiling": 32768},
                    {"family": "keyfunc", "levels": {3: 2}, "items_per_prompt": 2, "ceiling": 42000}]},
                "chat/main": {"flat": [
                    {"family": "attend", "variant": "host", "levels": {5: 1}, "element": "turn",
                     "ceiling_per_turn": 2500},
                    {"family": "istack", "variant": "write", "levels": {3: 2}, "items_per_prompt": 2,
                     "ceiling": 32768},
                    {"family": "session_state", "variant": "multi_hard", "levels": {10: 1},
                     "ceiling_per_turn": 2500}]}}}
    chains.check_gradeable(prof)
    answers: dict[str, str] = {}

    def build():
        for c in chains.chain_cases(prof):
            inst = chains.instance(c, 1, 1)
            case = chains.materialize(inst)
            if case.get("multi_items"):
                body = ""
                for k, (s_, sub) in enumerate(zip(inst["multi"], case["multi_items"]), 1):
                    g = s_["generator"]
                    ref = gen.get(g["type"]).reference(dict(g), release_seed=gen.release_seed(),
                                                       case_id=sub["id"])
                    body += f"=== ITEM {k} ===\n{ref}\n\n"
                answers[case["prompt"]] = body
            else:
                g = inst["generator"]
                ref = gen.get(g["type"]).reference(dict(g), release_seed=gen.release_seed(),
                                                   case_id=case["id"])
                for t, r in zip(case["turns"], split_turns(ref)):
                    answers[t] = r

    def chat(self, mid, msgs, **kw):
        text = next(m["content"] for m in reversed(msgs) if m["role"] == "user")
        if not answers:
            build()
        return ChatResult(response_text=answers.get(text, "395") if mid == "ref" else "I do not know.",
                          finish_reason="stop", served_model=mid, completion_tokens=7, prompt_tokens=30)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _fake_rig(monkeypatch, {"ref": None, "blank": None})
    monkeypatch.setattr(providers.Provider, "chat", chat)
    runner.run_models(_cfg(prof), [{"name": m, "model_id": m, "provider": "p"} for m in ("ref", "blank")],
                      chains.chain_cases(prof), skip_conformance=True)
    for m, want in (("ref", 1.0), ("blank", 0.0)):
        rows = config.load_transcripts(m)
        st = chains.section_stats(rows, prof)
        for part, s in st.items():
            assert s["complete"], (m, part, s)
            bad = {u: p.get("elements") for u, p in s["per_chain"].items()
                   if any(abs(e["score"] - want) > 1e-9 for e in (p.get("elements") or []))}
            assert not bad, (m, part, bad)
        assert st["work/main"]["elements"] == 4
        n_turn_els = len(chains.turn_weights(chains.materialize(chains.instance(
            next(c for c in chains.chain_cases(prof) if "attend" in c["id"]), 1, 1))["checks"]))
        assert st["chat/main"]["elements"] == n_turn_els + 2 + 1
        if m == "ref":
            assert st["work/main"]["share"] == 1.0
            assert st["chat/main"]["share"] == pytest.approx(1.0)
        else:
            assert st["work/main"]["share"] == 0.0 and st["chat/main"]["share"] == 0.0


def test_elemstats_table_two_models(tmp_path, monkeypatch, capsys):
    from crucibleforge import elemstats
    prof = _flatprof([{"family": "toychain", "levels": {2: 10, 4: 5}, "items_per_prompt": 5}])
    good = _multi_policy(lambda cid: "PASS")
    half = _multi_policy(lambda cid: None if cid.endswith("-i5") else
                         ("PASS" if int(cid.rsplit("-i", 1)[1]) % 2 else "nope"))
    _run(tmp_path, monkeypatch, prof, {"good": good, "half": half})
    assert elemstats.main(["good", "half"]) == 0
    out = capsys.readouterr().out
    assert "| good | toychain L2 | 10 | 2 | 1.00 |" in out
    assert "| half | toychain L2 | 10 | 2 | 0.40 |" in out and "| 20% |" in out   # i5 missing
    rows = elemstats.table({m: elemstats.elements(config.load_transcripts(m)) for m in ("good", "half")})
    pair = next(r for r in rows if r.get("pair") == "good - half" and r["stratum"] == "toychain L2")
    assert pair["n"] == 10 and pair["mean_d"] == pytest.approx(0.6)
    assert pair["tokens"] == pytest.approx((5 + 5) / 2 / 5)        # answer tokens 5-2=3 + think 2, / 5 items
    assert pair["rp_per_token"] == pytest.approx(1 / (pair["sd_d"] ** 2 * pair["tokens"]))


# ------------------------------------------------------------------ element: check

def _toy_checks(name: str):
    def materialize(g, *, release_seed, case_id):
        return {"prompt": f"TOY level={g['level']} key={g['key']} case={case_id}", "max_tokens": 64,
                "temperature": 0.0, "grader": "checks",
                "grader_config": {"checks": [{"id": x, "type": "regex", "pattern": x} for x in ("AA", "BB", "CC")]}}
    return types.SimpleNamespace(FAMILY=name, LEVELS=LEVELS, materialize=materialize,
                                 reference=lambda g, **kw: "AA BB CC", mutants=lambda g, **kw: ["nope"],
                                 grade=lambda a, m: 1.0)


@pytest.fixture
def _checktoy():
    gen.register(_toy_checks("toychecks"))
    chains._shape_reason.cache_clear()
    yield
    gen.unregister("toychecks")
    chains._shape_reason.cache_clear()


def test_element_check_single_items(tmp_path, monkeypatch, _checktoy):
    prof = _flatprof([{"family": "toychecks", "level": 2, "count": 4, "element": "check"}])
    chains.check_gradeable(prof)
    calls, _ = _run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "AA and CC"})
    rows = sorted(config.load_transcripts("m"), key=lambda r: r["case_id"])
    els = rows[0]["chain"]["elements"]
    assert [e["id"] for e in els] == ["FLAT-toychecks-001-cAA", "FLAT-toychecks-001-cBB", "FLAT-toychecks-001-cCC"]
    assert [e["score"] for e in els] == [1.0, 0.0, 1.0]
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["elements"] == 12 and st["units"] == 4 and st["share"] == pytest.approx(2 / 3)
    # identical pattern in every unit: within-unit sd only, ICC measured, element unit
    assert st["icc"] is not None and st["se_unit"] == "element"


def test_element_check_multi_item_prompt(tmp_path, monkeypatch, _checktoy):
    prof = _flatprof([{"family": "toychecks", "level": 2, "count": 4, "items_per_prompt": 2,
                       "element": "check"}])
    pol = _multi_policy(lambda cid: None if cid.endswith("-002-i2") else "AA BB")
    _run(tmp_path, monkeypatch, prof, {"m": pol})
    rows = sorted(config.load_transcripts("m"), key=lambda r: r["case_id"])
    e1, e2 = rows[0]["chain"]["elements"], rows[1]["chain"]["elements"]
    assert [e["id"] for e in e1][:3] == ["FLAT-toychecks-001-i1-cAA", "FLAT-toychecks-001-i1-cBB",
                                         "FLAT-toychecks-001-i1-cCC"]
    assert [e["score"] for e in e1] == [1, 1, 0, 1, 1, 0]
    assert [e["score"] for e in e2][3:] == [0, 0, 0] and {e["outcome"] for e in e2[3:]} == {"missing"}
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["elements"] == 12 and st["units"] == 2


def test_element_check_refused_without_sub_results():
    with pytest.raises(ConfigError, match="named sub-results"):
        chains.check_gradeable(_flatprof([{"family": "toychain", "level": 1, "count": 2, "element": "check"}]))


def test_element_check_session_and_board_judge_merge(_turntoy):
    prof = _flatprof([{"family": "toyturns", "level": 2, "count": 1, "element": "check"}])
    chains.check_gradeable(prof)
    c = chains.chain_cases(prof)[0]
    case = chains.materialize(chains.instance(c, 1, 1))
    rows = [{"case_id": case["id"], "turn": t, "response": "PASS" if t != 2 else "no", "finish_reason": "stop",
             "chain": dict(case["chain"])} for t in (1, 2, 3, 4)]
    chains.stamp_verdict(rows, case)
    els = rows[-1]["chain"]["elements"]
    assert {e["id"].rsplit("-c", 1)[1]: e["score"] for e in els} == {"t1": 1.0, "t2": 0.0, "t3": 1.0, "span": 0.0}
    # a judge stage result on the same row joins the unit's elements on the board
    rows[-1]["judge"] = {"elements": {"results": [{"id": "voice", "pass": True}, {"id": "heat", "pass": False}]}}
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["elements"] == 6 and st["share"] == pytest.approx(3 / 6)
    assert {e["id"] for e in st["per_chain"][c["id"]]["elements"]} >= {f"{c['id']}-jvoice", f"{c['id']}-jheat"}


@pytest.mark.parametrize("fam,var,lv,ok", [("keyfunc", None, 3, True), ("istack", "write", 3, True),
                                           ("session_state", "multi_hard", 10, True)])
def test_element_check_real_families(fam, var, lv, ok):
    item = {"family": fam, "level": lv, "count": 1, "element": "check"}
    if var:
        item["variant"] = var
    p = {"name": "t", "cases": {}, "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
         "chains": {"work/main": {"flat": [item]}}}
    chains.check_gradeable(p)


# ------------------------------------------------------------------ judged parts (NSFW)

def _toy_judged(name: str):
    """A single-turn session item with two code checks (groups constraint / continuity) and
    two judge elements, graded by a rubric (like theme_w)."""
    def materialize(g, *, release_seed, case_id):
        p = f"TOY level={g['level']} key={g['key']} case={case_id}"
        checks = [{"id": "adh", "turns": [1], "scope": "all", "weight": 1, "type": "require_regex",
                   "pattern": "AA", "group": "constraint"},
                  {"id": "cont", "turns": [1], "scope": "all", "weight": 1, "type": "require_regex",
                   "pattern": "BB", "group": "continuity"},
                  {"id": "info", "turns": [1], "scope": "all", "weight": 0, "type": "require_regex",
                   "pattern": "ZZ", "group": "constraint"}]
        return {"system": "s", "turns": [p], "checks": checks, "history": [], "user_name": "u",
                "judge_elements": [{"id": "q1", "text": "good"}, {"id": "q2", "text": "better"}],
                "rubric": "nsfw_craft", "programmatic": False, "explicit_required": True,
                "max_tokens": 64, "temperature": 0.0, "category": "nsfw", "generator": dict(g)}
    return types.SimpleNamespace(FAMILY=name, LEVELS=LEVELS, materialize=materialize,
                                 reference=lambda g, **kw: "AA BB", mutants=lambda g, **kw: ["nope"],
                                 grade=lambda a, m: 1.0)


@pytest.fixture
def _judgedtoy():
    gen.register(_toy_judged("toyjudged"))
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    yield
    gen.unregister("toyjudged")
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()


def _nsfwprof(**sec):
    return {"name": "t", "cases": {"nsfw": []},
            "scores": {"nsfw": {"parts": {"a": {"max": 4.5, "scoring": ["nsfw"], "cases": []}}}},
            "chains": {"nsfw/a": {"judged": True, "flat": [{"family": "toyjudged", "level": 1, "count": 4,
                                                             "element": "check"}], **sec}}}


def test_judged_part_opt_in_and_shape(_judgedtoy):
    p = _nsfwprof()
    chains.check_gradeable(p)
    no = _nsfwprof()
    no["chains"]["nsfw/a"].pop("judged")
    with pytest.raises(ConfigError, match="needs a judge"):
        chains.check_gradeable(no)
    bad = _nsfwprof()
    bad["chains"]["nsfw/a"]["flat"][0]["element"] = "item"
    with pytest.raises(ConfigError, match="element: check"):
        chains.sections(bad)
    with pytest.raises(ConfigError, match="add up to"):
        chains.sections(_nsfwprof(components={"q": {"points": 3.0, "source": "judge"}, "adh": 1.0}))


def test_judged_rows_carry_judge_fields_and_wait_for_the_judge(tmp_path, monkeypatch, _judgedtoy):
    from crucibleforge import judge
    prof = _nsfwprof(components={"quality": {"points": 3.0, "source": "judge"},
                                 "adherence": {"points": 0.75, "source": "code"},
                                 "continuity": {"points": 0.75, "source": "code", "groups": ["continuity"]}})
    prof["scores"]["nsfw"]["parts"]["a"]["scoring"] = ["nsfw"]
    _run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "AA only"})
    rows = sorted(config.load_transcripts("m"), key=lambda r: r["case_id"])
    assert len(rows) == 4
    r = rows[0]
    # (b) the judge finds the instance's elements and case fields on the row itself
    assert r["rubric"] == "nsfw_craft" and r.get("needs_judge")
    assert [e["id"] for e in judge.row_elements_all(r)] == ["q1", "q2"]
    assert judge.case_field(r, "explicit_required") is True
    els = r["chain"]["elements"]
    assert [(e["check"], e["score"], e.get("group")) for e in els] == [("adh", 1.0, "constraint"),
                                                                      ("cont", 0.0, "continuity")]
    assert r["chain"]["judged"] is True
    # (c) before the judge: every unit pending, the part partial, no points
    st = chains.section_stats(rows, prof)["nsfw/a"]
    assert st["pending_judge"] == 4 and not st["complete"] and st["points"] is None
    # the judge stage writes element results on the rows
    for i, x in enumerate(rows):
        x["judge"] = {"elements": {"results": [{"id": "q1", "pass": True}, {"id": "q2", "pass": i % 2 == 0}]}}
    st = chains.section_stats(rows, prof)["nsfw/a"]
    assert st["complete"] and st["elements"] == 16
    comp = st["components"]
    assert comp["quality"]["share"] == pytest.approx(0.75) and comp["adherence"]["share"] == 1.0
    assert comp["continuity"]["share"] == 0.0
    # (d) points = 3.0 x 0.75 + 0.75 x 1 + 0.75 x 0
    assert st["points"] == pytest.approx(3.0) and st["share"] == pytest.approx(3.0 / 4.5)
    assert st["se_points"] is not None and st["margin_points"] > 0


def test_engagement_multiplies_every_element(_judgedtoy, monkeypatch):
    prof = _nsfwprof(engagement="nsfw")
    case = chains.materialize(chains.instance(chains.chain_cases(prof)[0], 1, 1))
    rows = [{"case_id": case["id"], "turn": 1, "response": "AA BB", "finish_reason": "stop",
             "chain": dict(case["chain"]), "rubric": "nsfw_craft"}]
    chains.stamp_verdict(rows, case)
    rows[0]["judge"] = {"elements": {"results": [{"id": "q1", "pass": True}, {"id": "q2", "pass": True}]}}
    monkeypatch.setitem(chains.ENGAGEMENT, "nsfw", lambda row: 0.5)
    st = chains.section_stats(rows, prof)["nsfw/a"]
    one = st["per_chain"][case["chain"]["id"]]
    assert one["engagement"] == 0.5 and st["ended"] == 1
    monkeypatch.setitem(chains.ENGAGEMENT, "nsfw", lambda row: 0.0)
    assert chains.section_stats(rows, prof)["nsfw/a"]["points"] == 0.0


def test_weighted_points_se_matches_plain_mean_without_components():
    rnd = random.Random(5)
    cl = [[(rnd.random(), "all") for _ in range(rnd.randint(1, 4))] for _ in range(30)]
    pts, se, _ = chains._points_and_se(cl, [{"name": "all", "points": 10.0}])
    xs = [[x for x, _ in c] for c in cl]
    allx = [x for c in xs for x in c]
    assert pts == pytest.approx(10 * sum(allx) / len(allx))
    assert se == pytest.approx(10 * chains.mean_se(xs, "prompt"))
    pe, see, _ = chains._points_and_se([[p] for c in cl for p in c], [{"name": "all", "points": 10.0}])
    assert see == pytest.approx(10 * chains.mean_se(xs, "element"))


def test_deal_and_drop_reach_the_generator():
    p = _flatprof([{"family": "toychain", "level": 1, "count": 3, "deal": True, "drop": ["c2", "c1"],
                    "element": "item"}])
    cs = chains.chain_cases(p)
    gs = [chains.instance(c, 1, 1, release_seed="R")["generator"] for c in cs]
    assert [g["deal"]["slot"] for g in gs] == [0, 1, 2] and {g["deal"]["of"] for g in gs} == {3}
    assert len({g["deal"]["key"] for g in gs}) == 1 and all(g["exclude"] == ["c1", "c2"] for g in gs)
    blk = chains.instance(cs[0], 1, 1, release_seed="R")["chain"]
    assert blk["exclude"] == ["c1", "c2"] and blk["deal_of"] == 3


def test_average_unit_weighs_every_piece_the_same():
    cl = [[(1.0, "all")] * 4, [(0.0, "all")] * 1, [(0.5, "all")] * 2]
    pe, _, _ = chains._points_and_se(cl, [{"name": "all", "points": 1.0}])
    pu, seu, _ = chains._points_and_se(chains._unit_means(cl), [{"name": "all", "points": 1.0}])
    assert pe == pytest.approx(5 / 7) and pu == pytest.approx(0.5)
    assert seu == pytest.approx(chains.mean_se([[1.0], [0.0], [0.5]], "prompt"))
    with pytest.raises(ConfigError, match="average"):
        chains.sections(_flatprof([{"family": "toychain", "level": 1, "count": 2}], average="piece"))


_IMPLIED_W = {"AA": 2.0, "BB": 1.0, "CC": 0.25}


def test_element_weight_check_scores_the_weighted_share(tmp_path, monkeypatch, _checktoy):
    """IMPLIED shape: the grader's own check weights {2.0, 1.0, 0.25}; ``element_weight:
    check`` + ``average: unit`` = the mean over instances of each instance's weighted share
    (the score the family was screened on); per-element rows keep the stored weight."""
    from crucibleforge import graders
    orig = graders.grade

    def weighted(res, case):
        v = orig(res, case)
        for r in (v or {}).get("results") or []:
            r["weight"] = _IMPLIED_W[r["id"]]
        return v
    monkeypatch.setattr(graders, "grade", weighted)
    answers = {1: "AA", 2: "BB CC", 3: "AA BB CC", 4: "CC"}
    item = {"family": "toychecks", "level": 2, "count": 4, "element": "check"}
    prof = _flatprof([{**item, "element_weight": "check", "average": "unit"}])
    chains.check_gradeable(prof)
    _run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: answers[int(cid.split("-")[2])]})
    rows = sorted(config.load_transcripts("m"), key=lambda r: r["case_id"])
    assert [e["weight"] for e in rows[0]["chain"]["elements"]] == [2.0, 1.0, 0.25]
    st = chains.section_stats(rows, prof)["work/main"]
    per_unit = [2.0 / 3.25, 1.25 / 3.25, 1.0, 0.25 / 3.25]
    assert st["share"] == pytest.approx(sum(per_unit) / 4)
    assert st["elements"] == 12 and st["units"] == 4
    # instance = the SE unit: the SE is that of the four per-instance shares
    assert st["se"] == pytest.approx(chains.mean_se([[x] for x in per_unit], "prompt"))
    # default unchanged: every element weighs 1
    st1 = chains.section_stats(rows, _flatprof([item]))["work/main"]
    assert st1["share"] == pytest.approx(7 / 12)
    # pooled weighted share without per-instance averaging
    stp = chains.section_stats(rows, _flatprof([{**item, "element_weight": "check"}]))["work/main"]
    assert stp["share"] == pytest.approx((2.0 + 1.25 + 3.25 + 0.25) / 13.0)
    with pytest.raises(ConfigError, match="element_weight"):
        chains.sections(_flatprof([{"family": "toychain", "level": 1, "count": 2, "element_weight": "check"}]))
    with pytest.raises(ConfigError, match="element_weight"):
        chains.sections(_flatprof([item], element_weight="heavy"))


# ------------------------------------------------------------------ multi-item fences (EXP-20261113-47)

_FENCE_FX = Path(__file__).parent / "fixtures" / "istack_multi_fenced_20261114.json"


def _rc1_full(monkeypatch):
    from crucibleforge import profiles
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc1-night-full")
    return profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))


def _fx_case(monkeypatch, unit):
    prof = _rc1_full(monkeypatch)
    cc = next(c for c in chains.chain_cases(prof) if c["id"] == unit)
    inst = chains.instance(cc, 1, 1)
    return prof, inst, chains.materialize(inst)


def test_unwrap_fence_only_a_whole_wrapping_pair():
    assert chains.unwrap_fence("```\nA\n\nB\n```") == "A\n\nB"
    assert chains.unwrap_fence("```text\nA\n```") == "A"
    assert chains.unwrap_fence("A\n```\nB\n```") is None              # not wrapping all of it
    assert chains.unwrap_fence("```\nA\n```\nB\n```\nC\n```") is None  # more than one pair
    assert chains.unwrap_fence("plain prose") is None


def test_multi_item_fenced_prose_is_unwrapped(monkeypatch):
    """The stored reference reply of tonight's istack-write L3 5-item prompt: every piece came
    wrapped in ``` (the old header asked for a fenced block). Under the grader of the night the
    fences cost item 1 its gate (0.00) and items 2-5 their line checks (``stored_item_scores``);
    graded on the unwrapped piece they scored 0.87 / 1 / 1 / 1 / 1 (D's regrade_item2.py).
    The istack grader has moved since (a10d0f2, H1 fairness), so the item scores are computed
    from this tree's own grader, never pinned: unwrapping must grade each piece exactly as the
    bare piece, and never lower than the fenced one."""
    from crucibleforge import gen as _gen
    fx = json.loads(_FENCE_FX.read_text(encoding="utf-8"))
    _, inst, case = _fx_case(monkeypatch, fx["chain"]["id"])
    assert inst["chain"]["items"] == fx["chain"]["items"]            # the very instances that were asked
    row = {"response": fx["response"], "finish_reason": fx["finish_reason"]}
    segs = chains.split_items(fx["response"], 5)
    assert all(chains.unwrap_fence(seg) is not None for seg in segs)  # every piece came fenced
    fenced = [_gen.engine_score(s_, seg) for s_, seg in zip(case["multi_items"], segs)]
    bare = [_gen.engine_score(s_, chains.unwrap_fence(seg)) for s_, seg in zip(case["multi_items"], segs)]
    assert sum(fenced) < sum(bare)                                    # the defect: the fences cost credit
    els = chains.multi_elements(row, case)
    assert [e["score"] for e in els] == pytest.approx(bare, abs=1e-6)
    assert all(b >= f for b, f in zip(bare, fenced))
    assert all(e.get("unfenced") for e in els)
    # the new header for prose items does not ask for a fence
    assert not any(chains.wants_fence(s_) for s_ in case["multi_items"])
    assert case["prompt"].startswith(chains.MULTI_HEAD_PLAIN.format(n=5))
    assert "fenced" not in chains.MULTI_HEAD_PLAIN


def test_multi_item_code_fences_untouched(monkeypatch):
    """keyfunc asks for a ```python block: its header is the measured one and its segments
    are graded exactly as written, fenced or not."""
    _, inst, case = _fx_case(monkeypatch, "FLAT-keyfunc_L3-001")
    subs = case["multi_items"]
    assert all(chains.wants_fence(s_) for s_ in subs)
    assert case["prompt"].startswith(chains.MULTI_HEAD.format(n=len(subs)))
    seg = "```python\ndef f(xs, t):\n    return (0, t)\n```"
    assert chains.item_segment(subs[0], seg) == (seg, False)
    from crucibleforge.gen import keyfunc
    refs = [keyfunc.reference(s_["generator"], release_seed=gen.release_seed(), case_id=s_["id"]) for s_ in subs]
    bare = [r.replace("```python", "").replace("```", "").strip() for r in refs]
    # item 1 fenced, item 2 bare: the code grader accepts both, nothing is unwrapped
    reply = "\n".join([f"=== ITEM 1 ===\n```python\n{bare[0]}\n```", f"=== ITEM 2 ===\n{bare[1]}"]
                      + [f"=== ITEM {k} ===\n```python\n{bare[k - 1]}\n```" for k in range(3, len(subs) + 1)])
    els = chains.multi_elements({"response": reply, "finish_reason": "stop"}, case)
    assert [e["score"] for e in els] == pytest.approx([1.0] * len(subs))
    assert not any(e.get("unfenced") for e in els)


def test_regrade_rows_writes_beside_never_over(monkeypatch):
    """The multi-item fence fixture through the in-place regrade (regrade.plan, 2026-11-14): the
    stored row is untouched, the superseding copy carries the regraded verdict."""
    from crucibleforge import regrade
    fx = json.loads(_FENCE_FX.read_text(encoding="utf-8"))
    prof = _rc1_full(monkeypatch)
    specs = {c["id"]: c for c in chains.chain_cases(prof)}
    sp = specs[fx["chain"]["id"]]
    inst = chains.instance(sp, fx["chain"]["rung"], fx["chain"]["attempt"])
    assert inst["chain"]["seed_id"] == fx["chain"]["seed_id"] and inst["chain"]["items"] == fx["chain"]["items"]
    case = chains.materialize(inst)
    # a stored row carries the whole instance block (the fixture kept only its identity) and its prompt
    row = {"case_id": fx["case_id"], "response": fx["response"], "finish_reason": fx["finish_reason"],
           "prompt": case["prompt"], "system": case.get("system", ""), "chain_revision": "00000000",
           "model_label": "ref", "chain": {**inst["chain"], "score": 0.6254, "passed": True, "elements": [
               {"id": f"{fx['chain']['id']}-i{k}", "k": k, "score": s} for k, s in enumerate(fx["stored_item_scores"], 1)]}}
    before = json.dumps(row, sort_keys=True)
    recs = regrade.plan([row], specs, "fence removed for items whose task asks for no fence")
    assert json.dumps(row, sort_keys=True) == before                 # the stored row is untouched
    (r,) = recs
    assert r["status"] == "regrade" and r["score_before"] == pytest.approx(0.6254)
    # the expected verdict is the run's own path on this tree (the istack grader moves; a10d0f2)
    exp = copy.deepcopy(row)
    exp["chain"] = copy.deepcopy(inst["chain"])
    chains.stamp_verdict([exp], case)
    assert r["score_after"] == pytest.approx(exp["chain"]["score"]) and r["score_after"] > r["score_before"]
    new = r["rows"][-1]
    assert new["regraded_by"]["tool"] == "crucibleforge.regrade" and new["regraded_by"]["reason"]
    bad = copy.deepcopy(row)
    bad["chain"]["items"] = ["x"] * 5
    assert regrade.plan([bad], specs, "t")[0]["status"] == "refused"


# ------------------------------------------------------------------ judge_gate (N2's floor foils)

_FOILS = Path(__file__).parent / "fixtures" / "nsfw_floor_foils_20261114.json"
_VERDICT = {"YES": {"pass": True, "score": 2, "credit": 1.0}, "PARTIAL": {"pass": True, "score": 1, "credit": 0.5},
            "NO": {"pass": False, "score": 0, "credit": 0.0}}          # judge.set_pass on a 0/1/2 element


def _judge_res(verdicts):
    return {"elements": {"results": [{"id": v["id"], **_VERDICT[v["verdict"]]} for v in verdicts]}}


def _gate_rows(prof, verdict_sets):
    """One stamped row per unit of the part (code checks both pass), judged with the given
    verdict lists (None = no judge result yet)."""
    rows = []
    for cc, vs in zip(chains.chain_cases(prof), verdict_sets):
        case = chains.materialize(chains.instance(cc, 1, 1))
        r = [{"case_id": case["id"], "turn": 1, "response": "AA BB", "finish_reason": "stop",
              "chain": dict(case["chain"]), "rubric": "nsfw_craft"}]
        chains.stamp_verdict(r, case)
        if vs is not None:
            r[0]["judge"] = _judge_res(vs)
        rows += r
    return rows


_GATE_COMPS = {"quality": {"points": 3.0, "source": "judge"}, "adherence": {"points": 1.5, "source": "code"}}


def _ok(*ids_no):
    ids = ["top-act", "sustained", "state-fact", "voice", "no-soften", "sensory", "dynamic", "ending"]
    return [{"id": i, "verdict": "NO" if i in ids_no else "YES"} for i in ids]


def test_judge_gate_floor_foils_land_at_zero(_judgedtoy):
    foils = json.loads(_FOILS.read_text(encoding="utf-8"))["foils"]
    for kind in ("sfw", "other"):
        vs = foils[kind]["verdicts"]
        ungated = chains.section_stats(_gate_rows(_nsfwprof(components=_GATE_COMPS), [vs] * 4),
                                       _nsfwprof(components=_GATE_COMPS))["nsfw/a"]
        assert ungated["components"]["quality"]["share"] > 0.05          # what the screen measured: paid
        prof = _nsfwprof(components=_GATE_COMPS, judge_gate="top-act")
        st = chains.section_stats(_gate_rows(prof, [vs] * 4), prof)["nsfw/a"]
        assert st["complete"] and st["components"]["quality"]["share"] <= 0.05
        assert st["components"]["adherence"]["share"] == 1.0              # code elements untouched
        els = next(iter(st["per_chain"].values()))["elements"]
        judged = [e for e in els if "-j" in e["id"]]
        assert {e["outcome"] for e in judged if not e["id"].endswith("-jtop-act")} == {"gated"}
        assert next(e for e in judged if e["id"].endswith("-jtop-act"))["outcome"] == "wrong"


def test_judge_gate_passed_scores_exactly_as_before(_judgedtoy):
    sets = [_ok(), _ok("voice"), _ok("ending", "dynamic"), _ok("sustained")]
    plain = _nsfwprof(components=_GATE_COMPS)
    gated = _nsfwprof(components=_GATE_COMPS, judge_gate="top-act")
    a = chains.section_stats(_gate_rows(plain, sets), plain)["nsfw/a"]
    b = chains.section_stats(_gate_rows(gated, sets), gated)["nsfw/a"]
    for k in ("points", "share", "se_points", "elements", "complete"):
        assert a[k] == b[k]


def test_judge_gate_missing_is_not_ended(_judgedtoy):
    no_gate = [v for v in _ok("voice") if v["id"] != "top-act"]
    prof = _nsfwprof(components=_GATE_COMPS, judge_gate="top-act")
    st = chains.section_stats(_gate_rows(prof, [_ok(), _ok(), _ok(), no_gate]), prof)["nsfw/a"]
    assert st["judge_gate_missing"] == 1 and st["pending_judge"] == 1
    # partial: the part is scored over its 3 ended units only (never on the unit's code alone)
    assert not st["complete"] and st["ended"] == 3 and st["elements"] == 3 * 10
    miss = [u for u in st["per_chain"].values() if u.get("judge_gate_missing")]
    assert len(miss) == 1 and miss[0]["ended"] is False
    # without a gate the same results score normally (nothing is read as a fade)
    plain = _nsfwprof(components=_GATE_COMPS)
    assert chains.section_stats(_gate_rows(plain, [_ok(), _ok(), _ok(), no_gate]), plain)["nsfw/a"]["complete"]


def test_judge_gate_se_with_gated_units(_judgedtoy):
    foils = json.loads(_FOILS.read_text(encoding="utf-8"))["foils"]
    prof = _nsfwprof(components=_GATE_COMPS, judge_gate="top-act")
    sets = [foils["sfw"]["verdicts"], _ok(), foils["other"]["verdicts"], _ok("voice")]
    for unit in ("element", "prompt"):
        prof["chains"]["nsfw/a"]["se_unit"] = unit
        st = chains.section_stats(_gate_rows(prof, sets), prof)["nsfw/a"]
        assert st["complete"] and st["se_points"] is not None and math.isfinite(st["se_points"]) and st["se_points"] > 0
        assert st["components"]["quality"]["share"] == pytest.approx((0 + 1 + 0 + 7 / 8) / 4)


def test_judge_gate_config(_judgedtoy):
    with pytest.raises(ConfigError, match="judge_gate needs judged"):
        p = _nsfwprof(judge_gate="top-act")
        p["chains"]["nsfw/a"].pop("judged")
        chains.sections(p)
    with pytest.raises(ConfigError, match="judge_gate is a judge element id"):
        chains.sections(_nsfwprof(judge_gate=3))


def test_judge_elements_score_on_credit(_judgedtoy):
    """Flat judged parts score a judge element on its credit: a 0/1/2 element YES / PARTIAL /
    NO = 1 / 0.5 / 0; a result without credit (binary, old rows) keeps pass = 1 / 0."""
    blk = {"id": "U", "element": "check", "elements": []}
    res = {"elements": {"results": [{"id": v, **_VERDICT[v]} for v in ("YES", "PARTIAL", "NO")]
                        + [{"id": "b1", "pass": True}, {"id": "b0", "pass": False}]}}
    assert [e["score"] for e in chains.board_elements(blk, res)] == [1.0, 0.5, 0.0, 1.0, 0.0]
    # a PARTIAL gate passes: it keeps its 0.5 and gates nothing
    part = {"elements": {"results": [{"id": "top-act", **_VERDICT["PARTIAL"]}, {"id": "q", **_VERDICT["YES"]}]}}
    els = chains.board_elements(blk, part, "top-act")
    assert [(e["score"], e["outcome"]) for e in els] == [(0.5, "pass"), (1.0, "pass")]


def test_judge_credit_feeds_the_standard_error(_judgedtoy):
    """No components: the part is the plain mean of its elements, so its SE must equal the
    plain SE over the CREDIT values (2 code elements at 1 + 8 judge credits per unit)."""
    sets = [[{"id": i, "verdict": v} for i, v in zip(["a", "b", "c", "d", "e", "f", "g", "h"], vs)]
            for vs in (["YES"] * 8, ["PARTIAL"] * 8, ["YES"] * 4 + ["NO"] * 4, ["PARTIAL"] * 4 + ["NO"] * 4)]
    cr = {"YES": 1.0, "PARTIAL": 0.5, "NO": 0.0}
    clusters = [[1.0, 1.0] + [cr[v["verdict"]] for v in vs] for vs in sets]
    for unit in ("element", "prompt"):
        prof = _nsfwprof(se_unit=unit)
        st = chains.section_stats(_gate_rows(prof, sets), prof)["nsfw/a"]
        mean = sum(sum(c) for c in clusters) / 40
        assert st["share"] == pytest.approx(mean)
        assert st["se"] == pytest.approx(chains.mean_se(clusters, unit))


# ------------------------------------------------------------------ seed_as (paired single vs packed)

def test_seed_as_packed_set_asks_the_single_sets_instances(_checktoy):
    prof = _flatprof([{"family": "toychecks", "levels": {2: 6}, "name": "single", "element": "check"},
                      {"family": "toychecks", "levels": {2: 6}, "name": "packed", "items_per_prompt": 3,
                       "element": "check", "seed_as": "single"}])
    cases = {c["id"]: c for c in chains.chain_cases(prof)}
    single = [chains.instance(cases[f"FLAT-single_L2-{i:03d}"], 1, 1)["generator"]["key"] for i in range(1, 7)]
    packed = [k for p in (1, 2) for k in chains.instance(cases[f"FLAT-packed_L2-{p:03d}"], 1, 1)["chain"]["items"]]
    assert packed == single                                          # the same six briefs, in order
    assert chains.instance(cases["FLAT-packed_L2-001"], 1, 1)["chain"]["seed_ladder"] == "single_L2"
    # a family may seed on the case id (implied does): the aliased sub-item materialises with
    # the target unit's case id, so the brief text is identical, not just the key
    subs = chains.materialize(chains.instance(cases["FLAT-packed_L2-002"], 1, 1))["multi_items"]
    for k, sub in enumerate(subs, 4):
        one = chains.materialize(chains.instance(cases[f"FLAT-single_L2-{k:03d}"], 1, 1))
        assert sub["prompt"] == one["prompt"] and f"case=FLAT-single_L2-{k:03d}-r1-a1" in sub["prompt"]
        assert sub["id"] == f"FLAT-packed_L2-002-i{k - 3}"                 # row ids stay the set's own
    # without the alias the packed set draws its own seeds
    own = _flatprof([{"family": "toychecks", "levels": {2: 6}, "name": "packed", "items_per_prompt": 3,
                      "element": "check"}])
    c0 = chains.chain_cases(own)[0]
    assert not set(chains.instance(c0, 1, 1)["chain"]["items"]) & set(single)


def test_seed_as_refusals(_checktoy):
    base = {"family": "toychecks", "levels": {2: 3}, "name": "single", "element": "check"}
    with pytest.raises(ConfigError, match="seed_as names no other flat set"):
        chains.sections(_flatprof([base, {**base, "name": "p", "seed_as": "nope"}]))
    with pytest.raises(ConfigError, match="different family / variant / level"):
        one = {"family": "toychecks", "level": 2, "count": 3, "name": "one", "element": "check"}
        chains.sections(_flatprof([one, {**base, "name": "p", "levels": {3: 3}, "seed_as": "one"}]))
    with pytest.raises(ConfigError, match="seed_as is the name"):
        chains.sections(_flatprof([base, {**base, "name": "p", "seed_as": 5}]))


def test_elemstats_packing_pairs_single_and_packed_briefs(tmp_path, monkeypatch, _checktoy):
    """seed_as end to end: the single and the packed set ask the same 6 briefs; the stub
    answers each BRIEF (by its generator key) the same way in both arms except one, so the
    paired difference is known; elemstats --packing pairs them brief for brief."""
    from crucibleforge import elemstats
    prof = _flatprof([{"family": "toychecks", "levels": {2: 6}, "name": "single", "element": "check"},
                      {"family": "toychecks", "levels": {2: 6}, "name": "packed", "items_per_prompt": 3,
                       "element": "check", "seed_as": "single"}])
    keys = [chains.instance(c, 1, 1)["generator"]["key"] for c in chains.chain_cases(prof)
            if c["id"].startswith("FLAT-single")]
    ans = {k: ("AA BB CC" if i % 2 == 0 else "AA") for i, k in enumerate(keys)}
    worse = keys[0]                                     # the packed arm misses this brief's BB CC

    def pol(text):
        ks = re.findall(r"key=(\w+)", text)
        if len(ks) == 1:
            return ans[ks[0]]
        return "".join(f"=== ITEM {i} ===\n{'AA' if k == worse else ans[k]}\n" for i, k in enumerate(ks, 1))
    pol.full_text = True
    _run(tmp_path, monkeypatch, prof, {"m": pol})
    (r,) = elemstats.packing({"m": elemstats.briefs(config.load_transcripts("m"))})
    assert (r["single"], r["packed"], r["n"]) == ("single_L2", "packed_L2", 6)
    assert r["mean_single"] == pytest.approx((3 * 1 + 3 / 3) / 6)
    assert r["mean_d"] == pytest.approx(-(2 / 3) / 6)
    assert r["tok_packed"] > 0 and r["ran_out_single"] == r["ran_out_packed"] == 0
    assert "packed_L2" in elemstats.render_packing([r])


def test_seed_as_with_deal_draws_the_targets_hands(_checktoy):
    """deal: true (implied since t-implied5: distinct texts per release) -- an aliased set's
    unit i takes hand i-1 of the TARGET's partition, whatever its own count or packing."""
    base = {"family": "toychecks", "levels": {2: 6}, "element": "check", "deal": True}
    prof = _flatprof([{**base, "name": "single"},
                      {**base, "levels": {2: 3}, "name": "packed", "items_per_prompt": 3, "seed_as": "single"}])
    cases = {c["id"]: c for c in chains.chain_cases(prof)}
    single = [chains.instance(cases[f"FLAT-single_L2-{i:03d}"], 1, 1)["generator"] for i in (1, 2, 3)]
    packed = [s_["generator"] for s_ in chains.instance(cases["FLAT-packed_L2-001"], 1, 1)["multi"]]
    for a, b in zip(single, packed):
        assert a["key"] == b["key"] and a["deal"] == b["deal"]          # same key, same hand of the same partition
        assert b["deal"]["of"] == 6
    with pytest.raises(ConfigError, match="same deal / drop"):
        chains.sections(_flatprof([{**base, "name": "single"},
                                   {**base, "name": "p", "deal": False, "seed_as": "single"}]))


# ------------------------------------------------------------------ context accounting (multi-turn)

_TURN = re.compile(r"t=(\d+)")


def _session_prof(**extra):
    return _flatprof([{"family": "toyturns", "level": 2, "count": 2, "element": "turn",
                       "ceiling_per_turn": 2500, **extra}])


def test_history_budget_raises_the_load_floor(_turntoy):
    prof = _session_prof(history_budget=40000)
    ph = chains.chain_cases(prof)[0]
    assert ph["chain_spec"]["rungs"][0]["history_budget"] == 40000
    assert runner.fixed_budget({"defaults": {}}, ph) == 2500 + 40000
    assert runner.wide_context({"defaults": {}}, [ph]) == 2500 + 40000 + runner.ESCALATION_CTX_PAD
    # without it: the per-turn ceiling alone (unchanged revisions load as before)
    assert runner.fixed_budget({"defaults": {}}, chains.chain_cases(_session_prof())[0]) == 2500
    with pytest.raises(ConfigError, match="history_budget is a positive integer"):
        chains.sections(_session_prof(history_budget="lots"))
    with pytest.raises(ConfigError, match="history_budget is for a multi-turn item"):
        chains.sections(_flatprof([{"family": "toychain", "level": 2, "count": 3,
                                    "items_per_prompt": 3, "history_budget": 1000}]))


def _sess(cut_at: int | None = None, big_turn1: int = 0, refuse: str | None = None, cache: int | None = None):
    """A session policy: every turn says PASS; turn 1 reports ``big_turn1`` completion tokens
    (its thinking fills the history); turn ``cut_at`` is cut at its budget; ``refuse`` = the
    server's 400 text for turn 2."""
    def pol(text):
        t = int(_TURN.search(text).group(1))
        if refuse and t == 2:
            raise runner.RequestRejected(400, refuse)
        fin = "length" if t == cut_at else "stop"
        return ChatResult(response_text="PASS", finish_reason=fin, served_model="m",
                          completion_tokens=big_turn1 if (t == 1 and big_turn1) else 9,
                          prompt_tokens=20 * t, cache_read_tokens=cache, total_s=0.01)
    pol.full_text = True
    return pol


def test_context_unsent_turn_is_an_outcome_not_an_error(tmp_path, monkeypatch, _turntoy):
    """Turn 1's history (31,000 tokens) leaves no room in the 32,768 window: turn 2 is not sent,
    the session ends there with outcome "context"; turns 2-3 score 0, turn 1 keeps its pass."""
    prof = _session_prof()
    calls, _ = _run(tmp_path, monkeypatch, prof, {"m": _sess(big_turn1=31000)})
    assert len(calls["m"]) == 2                              # one turn per session was sent
    rows = config.load_transcripts("m")
    assert not any(r.get("error") for r in rows)
    unsent = [r for r in rows if r["finish_reason"] == "context"]
    assert len(unsent) == 2 and all(r["turn"] == 2 and r["context_exhausted"]["why"].startswith("not sent")
                                    for r in unsent)
    fin = [r for r in rows if r["chain"].get("elements")]
    assert len(fin) == 2 and all(r["needs_judge"] and r.get("checks") for r in fin)
    for r in fin:
        blk = r["chain"]
        assert blk["outcome"] == "context" and blk["passed"] is False and blk["context_exhausted"]
        assert [(e["score"], e["outcome"]) for e in blk["elements"]] == [
            (1.0, "pass"), (0.0, "context"), (0.0, "context")]
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["outcomes"] == {"pass": 2, "context": 4}
    # counted by the early stop like "ran out", never as a blank answer
    sig = earlystop.classify(unsent[0])
    assert sig["token_limit"] and not sig["blank"]
    # elemstats reports it apart from "ran out"
    (row,) = [x for x in elemstats.table({"m": elemstats.elements(rows)}) if "model" in x]
    assert row["context"] == pytest.approx(4 / 6) and row["ran_out"] == 0


def test_context_cut_by_the_window_is_not_ran_out(tmp_path, monkeypatch, _turntoy):
    """Turn 2 fits only with its budget clamped below the 2,500 ceiling (the window minus the
    history): sent at that budget; its cut is "context" (credit kept), the session ends, turn 3
    is not sent. A cut at the full ceiling stays "ran out"."""
    prof = _session_prof()
    calls, _ = _run(tmp_path, monkeypatch, prof, {"m": _sess(cut_at=2, big_turn1=29000)})
    sent = [mt for _c, mt in calls["m"]]
    assert sent.count(2500) == 2 and len(sent) == 4 and all(mt < 2500 for mt in sent if mt != 2500)
    rows = config.load_transcripts("m")
    cut = [r for r in rows if r["turn"] == 2]
    assert all(r["finish_reason"] == "length" and r["context_exhausted"]["budget"] < 2500 for r in cut)
    assert not any(r["turn"] == 3 for r in rows)
    for r in (x for x in rows if x["chain"].get("elements")):
        assert r["turn"] == 2 and r["chain"]["outcome"] == "context"
        assert [(e["score"], e["outcome"]) for e in r["chain"]["elements"]] == [
            (1.0, "pass"), (1.0, "context"), (0.0, "context")]
    # the same cut with room for the whole ceiling is the model's own "ran out"
    rows2_dir = tmp_path / "b"
    rows2_dir.mkdir()
    _run(rows2_dir, monkeypatch, prof, {"m": _sess(cut_at=2)})
    fin = [r for r in config.load_transcripts("m") if r["chain"].get("elements")]
    assert all(r["chain"]["outcome"] == "ran out" and not r["chain"].get("context_exhausted") for r in fin)
    assert [e["outcome"] for e in fin[0]["chain"]["elements"]] == ["pass", "ran out", "pass"]


def test_context_refusal_from_the_server_is_not_a_transport_error(tmp_path, monkeypatch, _turntoy):
    prof = _session_prof()
    _run(tmp_path, monkeypatch, prof,
         {"m": _sess(refuse='{"error":{"type":"exceed_context_size_error","message":"the request '
                            'exceeds the available context size, try increasing it"}}')})
    rows = config.load_transcripts("m")
    assert not any(r.get("error") for r in rows)
    ref = [r for r in rows if r["finish_reason"] == "context"]
    assert len(ref) == 2 and all("refused" in r["context_exhausted"]["why"] for r in ref)
    assert {r["chain"]["outcome"] for r in rows if r["chain"].get("elements")} == {"context"}
    # any other 400 is still the error it was
    d2 = tmp_path / "b"
    d2.mkdir()
    _run(d2, monkeypatch, prof, {"m": _sess(refuse="unsupported parameter: top_k")})
    rows2 = config.load_transcripts("m")
    assert not any(r.get("finish_reason") == "context" for r in rows2)
    assert any(r.get("error") for r in rows2)


def test_elemstats_prompt_and_cache_columns(tmp_path, monkeypatch, _turntoy):
    prof = _session_prof()
    _run(tmp_path, monkeypatch, prof, {"m": _sess(cache=15)})
    rows = config.load_transcripts("m")
    (row,) = [x for x in elemstats.table({"m": elemstats.elements(rows)}) if "model" in x]
    # per unit: 4 turns with prompt 20, 40, 60, 80 = 200; each turn reports 15 cached = 60
    assert row["prompt_unit"] == pytest.approx(200) and row["cached_unit"] == pytest.approx(60)
    assert row["cache_hit"] == pytest.approx(0.3)
    out = elemstats.render(elemstats.table({"m": elemstats.elements(rows)}))
    assert "| cache hit |" in out and "| 30% |" in out
    # a provider that reports no cache: no hit share (not 0%)
    d2 = tmp_path / "b"
    d2.mkdir()
    _run(d2, monkeypatch, prof, {"m": _sess()})
    (row2,) = [x for x in elemstats.table({"m": elemstats.elements(config.load_transcripts("m"))}) if "model" in x]
    assert row2["cache_hit"] is None and row2["cached_unit"] is None


# ------------------------------------------------------------------ bank tags (concept x band)

def test_cell_tags_parse_per_set_and_per_level():
    prof = _flatprof([{"family": "toychain", "name": "t", "concept": "C1",
                       "levels": {1: {"count": 2, "band": "low"}, 3: {"count": 1, "band": "mid"}}},
                      {"family": "toychain2", "level": 2, "count": 1, "concept": "C2", "band": "high"}],
                     ladders={"lad": [{"family": "toychain", "level": 4, "concept": "C1", "band": "high"}]},
                     chains=1)
    (sec,) = chains.sections(prof)
    tags = {fs["name"]: (fs["item"]["concept"], fs["item"]["band"]) for fs in sec["flat"]}
    assert tags == {"t_L1": ("C1", "low"), "t_L3": ("C1", "mid"), "toychain2": ("C2", "high")}
    assert sec["ladders"][0]["rungs"][0]["band"] == "high"
    blk = chains.instance({c["id"]: c for c in chains.chain_cases(prof)}["FLAT-t_L3-001"], 1, 1)["chain"]
    assert (blk["concept"], blk["band"]) == ("C1", "mid")


@pytest.mark.parametrize("items, msg", [
    ([{"family": "toychain", "level": 1, "count": 1, "concept": "C1", "band": "low"},
      {"family": "toychain2", "level": 1, "count": 1}], "untagged: .*toychain2 \\(concept, band\\)"),
    ([{"family": "toychain", "level": 1, "count": 1, "concept": "C1"}], "untagged: .*\\(band\\)"),
    ([{"family": "toychain", "level": 1, "count": 1, "concept": "coding", "band": "low"}], "concept is a blueprint id"),
    ([{"family": "toychain", "level": 1, "count": 1, "concept": "C1", "band": "hard"}], "band is one of"),
    ([{"family": "toychain", "levels": {1: {"count": 1, "weight": 2}}}], "levels\\[1\\] takes count"),
])
def test_tagged_profile_refuses_untagged_or_bad_cells(items, msg):
    with pytest.raises(ConfigError, match=msg):
        chains.sections(_flatprof(items))


def test_untagged_profiles_still_load():
    assert chains.sections(_flatprof([{"family": "toychain", "level": 1, "count": 2}]))


def test_elemstats_groups_by_concept_and_band(tmp_path, monkeypatch):
    prof = _flatprof([{"family": "toychain", "level": 1, "count": 2, "concept": "C1", "band": "low"},
                      {"family": "toychain", "level": 3, "count": 2, "concept": "C1", "band": "mid", "name": "hi"},
                      {"family": "toychain2", "level": 2, "count": 2, "concept": "C2", "band": "mid"}])
    _run(tmp_path, monkeypatch, prof, {"m": MID})
    per = {"m": elemstats.elements(config.load_transcripts("m"))}
    by_c = {r["stratum"]: r for r in elemstats.table(per, "concept") if "model" in r}
    assert set(by_c) == {"C1", "C2"} and by_c["C1"]["n"] == 4 and by_c["C1"]["mean"] == pytest.approx(0.5)
    by_b = {r["stratum"]: r["n"] for r in elemstats.table(per, "band") if "model" in r}
    assert by_b == {"low": 2, "mid": 4}
    assert {r["stratum"] for r in elemstats.table(per, "concept-band") if "model" in r} == {"C1 low", "C1 mid", "C2 mid"}


# ------------------------------------------------------------------ ran_out: zero | exclude per cell

def _cut_pol(n_ok=3):
    def pol(text):
        body = "".join(f"=== ITEM {k} ===\nPASS\n" for k in range(1, n_ok + 1))
        return ChatResult(response_text=body, finish_reason="length", served_model="m",
                          completion_tokens=9, prompt_tokens=20)
    pol.full_text = True
    return pol


@pytest.mark.parametrize("rule, share", [(None, 3 / 5), ("zero", 3 / 5), ("exclude", 1.0)])
def test_ran_out_rule_on_the_board(tmp_path, monkeypatch, rule, share):
    """Items 3-5 ran out (item 3 cut mid-answer keeps its credit): `zero` (default) scores them
    as earned (0 for items never reached), `exclude` reports them and scores the rest; the
    ran-out share (60%) is reported and flagged either way."""
    it = {"family": "toychain", "level": 2, "count": 5, "items_per_prompt": 5, "ceiling": 2000}
    if rule:
        it["ran_out"] = rule
    prof = _flatprof([it])
    _run(tmp_path, monkeypatch, prof, {"m": _cut_pol()})
    rows = config.load_transcripts("m")
    assert rows[0]["chain"].get("ran_out") == (None if rule in (None, "zero") else "exclude")
    st = chains.section_stats(rows, prof)["work/main"]
    want = (3 / 5 * 5 + 0) / 5 if rule != "exclude" else (2 / 2)
    assert st["share"] == pytest.approx(share if rule != "exclude" else want)
    assert st["complete"] and st["outcomes"]["ran out"] == 3
    (s,) = st["sets"].values()
    assert s["share"] == pytest.approx(0.6) and s["ceiling_too_low"]
    assert st["ceiling_too_low"] == ["toychain"]


def test_ran_out_exclude_unit_with_nothing_left(tmp_path, monkeypatch):
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 5, "items_per_prompt": 5,
                       "ceiling": 2000, "ran_out": "exclude"},
                      {"family": "toychain2", "level": 2, "count": 1}])
    _run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: ChatResult(
        response_text="", finish_reason="length", served_model="m", completion_tokens=9, prompt_tokens=20)
        if "toychain-" in cid or "-i" in cid else "PASS"})
    st = chains.section_stats(config.load_transcripts("m"), prof)["work/main"]
    assert st["complete"] and st["ran_out_excluded_units"] == 1 and st["ended"] == 2
    assert st["share"] == pytest.approx(1.0)             # only toychain2's PASS is scored


def test_ran_out_rule_config():
    with pytest.raises(ConfigError, match="ran_out is one of"):
        chains.sections(_flatprof([{"family": "toychain", "level": 2, "count": 1, "ran_out": "maybe"}]))
    (sec,) = chains.sections(_flatprof([{"family": "toychain", "name": "t",
                                         "levels": {1: 1, 5: {"count": 1, "ran_out": "exclude"}}}]))
    assert [fs["item"].get("ran_out") for fs in sec["flat"]] == [None, "exclude"]


def test_elemstats_reports_ran_out_and_excludes_under_the_rule(tmp_path, monkeypatch):
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 5, "items_per_prompt": 5,
                       "ceiling": 2000, "ran_out": "exclude"}])
    _run(tmp_path, monkeypatch, prof, {"m": _cut_pol()})
    (r,) = [x for x in elemstats.table({"m": elemstats.elements(config.load_transcripts("m"))}) if "model" in x]
    assert r["ran_out"] == pytest.approx(0.6) and r["ceiling_too_low"] and r["excluded"] == 3
    assert r["n"] == 2 and r["mean"] == pytest.approx(1.0)
    assert "(ceiling too low)" in elemstats.render([r])


# ------------------------------------------------------------------ routing: locator -> module by score

from crucibleforge import routing  # noqa: E402

_RCAL = {"version": "rt1", "cells": {"toychain:L1": {"b": -1.0}, "toychain:L2": {"b": 0.0},
                                     "toychain:L3": {"b": 1.0}, "toychain2:L4": {"b": 2.0}}}


def _routed_prof(tmp_path, **r):
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps(_RCAL), encoding="utf-8")
    prof = _flatprof([{"family": "toychain", "level": 2, "count": 4, "name": "loc"},
                      {"family": "toychain", "level": 1, "count": 4, "name": "lo"},
                      {"family": "toychain", "level": 3, "count": 4, "name": "md"},
                      {"family": "toychain2", "level": 4, "count": 2, "name": "pk"}])
    prof["routing"] = {"calibration": str(cal), "locator": ["loc"],
                       "modules": [{"name": "low", "sets": ["lo"]}, {"name": "mid", "sets": ["md"]},
                                   {"name": "packs", "sets": ["pk"], "unlock": True}],
                       "cuts": [-1.0, 1.0], "straddle": "none", **r}
    return prof


def _run_routed(tmp_path, monkeypatch, prof, policies):
    from crucibleforge import cli
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    calls = _fake_rig(monkeypatch, policies)
    cfg = _cfg(prof)
    entries = [{"name": m, "model_id": m, "provider": "p"} for m in policies]
    args = types.SimpleNamespace(smoke=False, skip_conformance=True)
    return calls, cli._run_routed(cfg, entries, chains.chain_cases(prof), prof, args)


@pytest.mark.parametrize("pol, module, unlock", [
    (lambda lv, cid: "nope", "low", False),                                              # theta far below -1
    (lambda lv, cid: "PASS" if int(cid.split("-")[2]) % 2 else "nope", "mid", False),    # half the locator: theta 0
    (lambda lv, cid: "PASS", "packs", True),                                             # clears Main
])
def test_routing_by_locator_score(tmp_path, monkeypatch, pol, module, unlock):
    prof = _routed_prof(tmp_path)
    calls, summary = _run_routed(tmp_path, monkeypatch, prof, {"m": pol})
    r = summary["m"]["routing"]
    assert r["routed"] and [x["name"] for x in r["modules"]] == [module] and r["unlock"] is unlock
    assert r["rule"] == "score" and r["calibration"]["version"] == "rt1" and r["locator_units"] == 4
    rows = config.load_transcripts("m")
    sets = {row["chain"]["ladder"] for row in rows}
    want = {"low": "lo", "mid": "md", "packs": "pk"}[module]
    assert sets == {"loc", want}                                    # only the routed module ran
    mod_rows = [row for row in rows if row["chain"]["ladder"] == want]
    assert mod_rows and all(row["route"]["module"] == module and row["route"]["theta"] == r["theta"]
                            for row in mod_rows)
    assert not any("route" in row for row in rows if row["chain"]["ladder"] == "loc")
    # D's request (2026-11-14): every row of a routed run says which stage made it
    assert {row["stage"] for row in rows if row["chain"]["ladder"] == "loc"} == {"locator"}
    assert {row["stage"] for row in mod_rows} == {"module"}
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["complete"] and st["units"] == 4 + (2 if module == "packs" else 4)   # untaken modules are not pending


def test_routed_modules_take_the_same_instances_for_every_model(tmp_path, monkeypatch):
    prof = _routed_prof(tmp_path)
    same = lambda lv, cid: "PASS" if int(cid.split("-")[2]) % 2 else "nope"   # noqa: E731
    _run_routed(tmp_path, monkeypatch, prof, {"a": same, "b": same})
    ids = {m: sorted((r["case_id"], r["chain"]["seed_id"]) for r in config.load_transcripts(m)
                     if r["chain"]["ladder"] == "md") for m in ("a", "b")}
    assert ids["a"] == ids["b"] and len(ids["a"]) == 4


def test_routing_straddle_gives_half_of_each_neighbour(tmp_path):
    prof = _routed_prof(tmp_path, straddle="half")
    sp = routing.spec(prof)
    assert routing.choose(-0.9, 0.3, sp)["modules"] == [{"name": "low", "share": "half"},
                                                         {"name": "mid", "share": "half"}]
    assert routing.choose(0.0, 0.3, sp)["modules"] == [{"name": "mid", "share": "all"}]
    top = routing.choose(1.2, 0.5, sp)
    # a straddle of the top cut takes half-modules and NO packs, on either side of the cut
    assert top["modules"] == [{"name": "mid", "share": "half"}, {"name": "packs", "share": "half"}]
    assert not top["unlock"] and not routing.choose(0.8, 0.5, sp)["unlock"]
    assert routing.choose(1.6, 0.5, sp) == {"modules": [{"name": "packs", "share": "all"}], "unlock": True,
                                            "straddled_cut": None}
    cases = chains.chain_cases(prof)
    half = routing.stage_cases(cases, sp, {"modules": [{"name": "mid", "share": "half"}], "unlock": False})
    assert sorted(c["id"] for c in half) == ["FLAT-md-001", "FLAT-md-002"]       # the same first half for everyone
    nostr = routing.spec(_routed_prof(tmp_path))
    assert routing.choose(-0.9, 0.3, nostr)["modules"] == [{"name": "mid", "share": "all"}]


@pytest.mark.parametrize("bad, msg", [
    ({"locator": ["nope"]}, "no flat set or ladder named"),
    ({"cuts": [1.0]}, "cuts is 2 ascending"),
    ({"cuts": [1.0, -1.0]}, "cuts is 2 ascending"),
    ({"modules": [{"name": "low", "sets": ["lo", "loc"]}, {"name": "mid", "sets": ["md"]},
                  {"name": "packs", "sets": ["pk"], "unlock": True}]}, "already in the locator"),
    ({"modules": [{"name": "low", "sets": ["lo"], "unlock": True}, {"name": "mid", "sets": ["md"]},
                  {"name": "packs", "sets": ["pk"]}]}, "only the top module"),
    ({"calibration": ""}, "calibration names"),
    ({"straddle": "third"}, "straddle is one of"),
])
def test_routing_config_refused(tmp_path, bad, msg):
    with pytest.raises(ConfigError, match=msg):
        routing.spec(_routed_prof(tmp_path, **bad))


def test_routing_reads_no_model_identity():
    """Routing is by score, never by name (as the bonus-pack activation path)."""
    import ast
    import inspect
    import textwrap
    ident = {"model_id", "model_label", "vendor", "provider", "provider_type", "served_model", "name_gate"}
    for fn in (routing.spec, routing.choose, routing.plan, routing.locator_ability, routing.stage_cases,
               routing.taken, routing.route_of, routing.run_id, routing._same_spec):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        hits = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in ident}
        hits |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ident}
        hits |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in ident}
        assert not hits, (fn.__name__, hits)
    # and the route of two models with the same locator rows is the same
    sp = {"cuts": [-1.0, 1.0], "straddle": "half",
          "modules": [{"name": "low", "unlock": False}, {"name": "mid", "unlock": False},
                      {"name": "packs", "unlock": True}]}
    assert routing.choose(0.3, 0.2, sp) == routing.choose(0.3, 0.2, sp)


def test_unroutable_locator_blocks(tmp_path, monkeypatch):
    prof = _routed_prof(tmp_path)
    (tmp_path / "cal.json").write_text(json.dumps({"version": "x", "cells": {"other:L1": {"b": 0.0}}}), encoding="utf-8")
    _c, summary = _run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "PASS"})
    # never route on an uncalibrated cell: refused before the locator spends a turn
    why = summary["m"]["routing"]["why"]
    assert summary["m"]["routing"]["routed"] is False and "toychain:L2" in why and "not calibrated" in why
    assert config.load_transcripts("m") == []
    # a table that places the locator cell routes (the module cells need not be calibrated)
    (tmp_path / "cal.json").write_text(json.dumps({"version": "y", "cells": {"toychain:L2": {"b": 0.0}}}), encoding="utf-8")
    sp = routing.spec(prof)
    assert routing.uncalibrated(chains.chain_cases(prof), sp, routing.calibration(sp)[0]) == []


def test_routed_run_is_one_run_and_scores_identically_by_its_id(tmp_path, monkeypatch):
    """Both stages carry ONE bench_run_id; selecting the run by that id scores what the
    whole stored file scores (nothing of the run sits under another id)."""
    from crucibleforge import scale
    prof = _routed_prof(tmp_path)
    pol = lambda lv, cid: "PASS" if int(cid.split("-")[2]) % 2 else "nope"   # noqa: E731
    _run_routed(tmp_path, monkeypatch, prof, {"m": pol})
    rows = config.load_transcripts("m")
    rids = {r["bench_run_id"] for r in rows}
    assert len(rids) == 1 and {r["chain"]["ladder"] for r in rows} == {"loc", "md"}
    (rid,) = rids
    cal = scale.normalise(_RCAL)
    by_id = scale.score_run(scale.units(rows, bench_runs={rid}), cal)
    whole = scale.score_run(scale.units(rows), cal)
    assert by_id == whole and sum(v["units"] for v in by_id.values()) == 8
    assert json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))["bench_run_id"] == rid


def test_resumed_routed_run_keeps_its_stored_route(tmp_path, monkeypatch):
    """Module rows exist: a resume never re-routes from a re-estimated locator; it reuses the
    stored route (and says so) and continues the same run id."""
    prof = _routed_prof(tmp_path)
    _run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "nope"})          # -> low
    rows = config.load_transcripts("m")
    (rid,) = {r["bench_run_id"] for r in rows}
    stored = routing.route_of(rows)
    assert stored["modules"] == [{"name": "low", "share": "all"}]
    # the locator now reads as a clear pass (re-estimated it would route to packs)
    flipped = [dict(r, chain=dict(r["chain"], score=1.0, passed=True)) if r["chain"]["ladder"] == "loc" else r
               for r in rows]
    p = routing.plan(flipped, chains.chain_cases(prof), prof)
    assert p["stage"] == "module" and p["route"]["reused"] and p["bench_run_id"] == rid
    assert p["route"]["modules"] == stored["modules"] and p["route"]["theta"] == stored["theta"]
    assert {c["chain_spec"]["ladder"] for c in p["cases"]} == {"lo"}
    # and through the CLI: a second run continues the same run on the same module
    _c, summary = _run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "PASS"})
    r = summary["m"]["routing"]
    assert r["reused"] and [m["name"] for m in r["modules"]] == ["low"]
    rows2 = config.load_transcripts("m")
    assert {x["bench_run_id"] for x in rows2} == {rid}
    assert {x["chain"]["ladder"] for x in rows2} == {"loc", "lo"}


def test_changed_routing_spec_does_not_reuse_an_old_route(tmp_path, monkeypatch):
    prof = _routed_prof(tmp_path)
    _run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "nope"})
    rows = config.load_transcripts("m")
    moved = _routed_prof(tmp_path, cuts=[-5.0, -4.0])          # new cuts: a new routing, re-decided
    p = routing.plan(rows, chains.chain_cases(moved), moved)
    assert p["stage"] == "module" and not p["route"].get("reused")


# ------------------------------------------------------------------ routing: the route opens the packs

def _pack_prof(tmp_path, packs=("work/trace",), unlock_part=False):
    prof = _routed_prof(tmp_path)
    prof["scores"]["work"]["parts"]["trace"] = {"max": 0, "scoring": ["coding"],
                                                **({"unlock_part": "work"} if unlock_part else {})}
    prof["routing"]["modules"][-1]["packs"] = list(packs)
    return prof


def _card(prof, share, route, main=None):
    """``main``: the work/main chain part's share (complete). Since c513c5e an ``unlock_part`` pack's
    gate is the owning score's weighted Main std_share and stays None while a Main part is pending."""
    from crucibleforge import report

    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None, "components": {},
               "weights": dict(w)}
        out.update({g: share for g in w})
        return out
    from crucibleforge import ladder
    sets = {k: v for k, v in ladder.discover(prof, dirs=[ladder.ROOT_DIR / "cases" / "ladder"]).items()
            if k == "work/trace"}                                   # the repo's trace pack
    mp = pytest.MonkeyPatch()
    mp.setattr(report, "scorecard", fake)
    mp.setattr(report, "_ladder_sets", lambda _p: sets)
    try:
        sc = report._declared_weights(prof, None, None)[0]
        src = {"route": route} if route is not None else {}
        if main is not None:
            src["chain_stats"] = {"work/main": {"share": main, "complete": True}}
        return report.points_scorecard(src, sc, prof)
    finally:
        mp.undo()


def test_routed_packs_open_on_the_route_alone(tmp_path):
    prof = _pack_prof(tmp_path)
    assert routing.pack_parts(prof) == {"work/trace"}
    up, down = {"unlock": True, "modules": [{"name": "packs"}]}, {"unlock": False, "modules": [{"name": "mid"}]}
    for share in (0.0, 100.0):                    # the section share never opens or shuts a routed pack
        st_up = _card(prof, share, up)["ladder"]["work"]["trace"]
        st_dn = _card(prof, share, down)["ladder"]["work"]["trace"]
        assert st_up["opened"] is True and st_up["unlocked_by"] == "route"
        assert {x["state"] for x in st_up["sets"]} == {"pending"}
        assert st_dn["opened"] is False and {x["state"] for x in st_dn["sets"]} == {"locked"}
    nothing = _card(prof, 100.0, None)["ladder"]["work"]["trace"]       # no route yet: closed
    assert nothing["opened"] is False and {x["state"] for x in nothing["sets"]} == {"locked"}


def test_unrouted_packs_keep_the_share_gate(tmp_path):
    prof = _routed_prof(tmp_path)              # routing, but trace is not a routed pack
    prof["scores"]["work"]["parts"]["trace"] = {"max": 0, "scoring": ["coding"], "unlock_part": "work"}
    top = {"unlock": True, "modules": [{"name": "packs", "share": "all"}]}
    for share, want in ((80.0, "pending"), (79.0, "locked")):
        prof["ladder"] = {"unlock": 0.8}
        d = _card(prof, share, top, main=share / 100)["ladder"]["work"]["trace"]
        assert "opened" not in d and {x["state"] for x in d["sets"]} == {want}
        assert d["pack_gate"]["opened"] is (want == "pending") and d["pack_gate"]["threshold"] == 0.8
    # 2026-11-14 (lead): behind the route's unlock_from module (default: the top one), whatever the share
    d = _card(prof, 100.0, {"unlock": False, "modules": [{"name": "mid", "share": "all"}]})["ladder"]["work"]["trace"]
    assert {x["state"] for x in d["sets"]} == {"locked"} and d["pack_gate"]["reason"] == "route below packs"


def test_routed_pack_due_on_the_run_side(tmp_path, monkeypatch):
    """cli.ladder_todo (the run side) reads the same ``opened``: due when the route unlocks."""
    from crucibleforge import cli, ladder
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    prof = _pack_prof(tmp_path)
    sets = {k: v for k, v in ladder.discover(prof, dirs=[ladder.ROOT_DIR / "cases" / "ladder"]).items()
            if k == "work/trace"}
    cfg = _cfg(prof)
    monkeypatch.setattr(ladder, "set_cases", lambda _p, key, n, **kw: [{"id": f"{key}#{n}-q", "category": "coding"}])
    # the route decides whatever the share; None (not a routed pack) = the share gate as before
    for std, opened, want in ((0.0, True, ["work/trace#1"]), (1.0, False, []),
                              (1.0, None, ["work/trace#1"]), (0.5, None, [])):
        _c, _j, names = cli.ladder_todo("m", cfg, prof, sets, 0.8,
                                        progress={"work/trace": {"std": std, "sets": {}, "opened": opened}})
        assert names == want, (std, opened)
    assert ladder.due(0.0, {}, sets["work/trace"], 0.8, opened=True) == [1]
    assert ladder.due(1.0, {}, sets["work/trace"], 0.8, opened=False) == []


@pytest.mark.parametrize("packs, unlock_part, where, msg", [
    (["work/trace"], True, -1, "also declares unlock_part"),
    (["work/nope"], False, -1, "no part"),
    (["work/main"], False, -1, "max > 0"),
    (["trace"], False, -1, "<score>/<part>"),
    (["work/trace"], False, 0, "only the unlock module names packs"),
])
def test_routed_packs_refused_at_load(tmp_path, packs, unlock_part, where, msg):
    prof = _pack_prof(tmp_path, packs=[], unlock_part=unlock_part)
    prof["routing"]["modules"][-1].pop("packs")
    prof["routing"]["modules"][where]["packs"] = packs
    with pytest.raises(ConfigError, match=msg):
        routing.spec(prof)


def test_board_stats_carry_the_stored_route(tmp_path, monkeypatch):
    from crucibleforge import report
    prof = _pack_prof(tmp_path)
    _run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "PASS"})     # clears Main
    rows = config.load_transcripts("m")
    s = report.stats_from_tier_rows("m", None, prof, {1: rows, 2: [], 3: []}, None, None)
    assert s["route"]["unlock"] is True and routing.opened(s["route"])


def test_board_counts_empty_stop_turns_per_set(tmp_path, monkeypatch):
    prof = _prof(n=2)
    _run(tmp_path, monkeypatch, prof, {"ace": ACE})
    rows = config.load_transcripts("ace")
    s0 = chains.section_stats(rows, prof)["work/main"]
    assert s0["empty_stop"] == 0
    rows[0] = dict(rows[0], empty_stop={"reasoning_chars": 5})
    s = chains.section_stats(rows, prof)["work/main"]
    lad = rows[0]["chain"]["ladder"]
    assert s["empty_stop"] == 1 and s["sets"][lad]["empty_stop"] == 1
    assert s["share"] == s0["share"]                                # counted, never re-scored here
    assert s["margin_90"] == pytest.approx(chains.Z90 / chains.Z95 * s["margin_points"]) if s["margin_points"] else True


# ------------------------------------------------------------------ per-turn judge_gate ("t{n}-act")

def _turn_judge(missing=(), no=()):
    """Three turns, each with a gate (t<n>-act) and two craft elements; ``no`` = ids judged NO."""
    ids = [f"t{n}-{k}" for n in (1, 2, 3) for k in ("act", "tone", "detail")]
    return {"elements": {"results": [{"id": i, **_VERDICT["NO" if i in no else "YES"]}
                                     for i in ids if i not in missing]}}


def test_per_turn_gate_no_gates_only_its_own_turn():
    blk = {"id": "U", "element": "check", "elements": [{"id": "U-c1", "score": 1.0, "outcome": "pass"}]}
    els = {e["id"]: e for e in chains.board_elements(blk, _turn_judge(no={"t2-act"}), "t{n}-act")}
    assert els["U-c1"]["score"] == 1.0                                         # code untouched
    assert els["U-jt2-act"]["outcome"] == "wrong" and els["U-jt2-act"]["score"] == 0.0
    assert els["U-jt2-tone"]["outcome"] == els["U-jt2-detail"]["outcome"] == "gated"
    for n in (1, 3):                                                            # other turns keep their credit
        assert all(els[f"U-jt{n}-{k}"]["score"] == 1.0 for k in ("act", "tone", "detail"))
    assert not chains.judge_gate_missing(_turn_judge(no={"t2-act"}), "t{n}-act")


def test_per_turn_gate_missing_marks_only_that_turn_and_unit_not_ended():
    j = _turn_judge(missing={"t3-act"})
    assert chains.judge_gate_missing(j, "t{n}-act")
    blk = {"id": "U", "element": "check", "elements": []}
    els = {e["id"]: e for e in chains.board_elements(blk, j, "t{n}-act")}
    assert els["U-jt3-tone"].get("judge_gate_missing") and els["U-jt3-detail"].get("judge_gate_missing")
    assert not any(els[f"U-jt{n}-{k}"].get("judge_gate_missing") for n in (1, 2) for k in ("act", "tone"))
    assert all(e["score"] == 1.0 for e in els.values())                         # nothing gated, nothing read as a fade


def test_per_turn_gate_map_form_and_partial_passes():
    j = _turn_judge()
    j["elements"]["results"][0].update(_VERDICT["PARTIAL"])                    # t1-act PARTIAL: passes, keeps 0.5
    blk = {"id": "U", "element": "check", "elements": []}
    els = {e["id"]: e for e in chains.board_elements(blk, j, {"gate": "t{n}-act", "scope": "t{n}-"})}
    assert els["U-jt1-act"]["score"] == 0.5 and els["U-jt1-tone"]["score"] == 1.0
    # the single-id form is unchanged: one gate for the whole unit
    one = {e["id"]: e for e in chains.board_elements(blk, _turn_judge(no={"t1-act"}), "t1-act")}
    assert {one[f"U-jt{n}-tone"]["outcome"] for n in (1, 2, 3)} == {"gated"}


def test_per_turn_gate_config(_judgedtoy):
    chains.sections(_nsfwprof(judge_gate="t{n}-act"))
    chains.sections(_nsfwprof(judge_gate={"gate": "t{n}-act", "scope": "t{n}-"}))
    for bad in ({"gate": "t{n}-act"}, {"gate": "act", "scope": "t{n}-"}, {"gate": "t{n}-act", "scope": "x-"},
                "t{n}", {"gate": "t{n}-act", "scope": "t{n}-", "x": 1}):
        with pytest.raises(ConfigError, match="judge_gate"):
            chains.sections(_nsfwprof(judge_gate=bad))


def test_per_turn_gate_on_the_board(_judgedtoy):
    prof = _nsfwprof(components=_GATE_COMPS, judge_gate="t{n}-act")
    rows = _gate_rows(prof, [None] * 4)
    for r in rows:
        r["judge"] = _turn_judge(no={"t2-act"})
    rows[-1]["judge"] = _turn_judge(missing={"t3-act"})
    st = chains.section_stats(rows, prof)["nsfw/a"]
    assert st["judge_gate_missing"] == 1 and not st["complete"] and st["ended"] == 3
    # turn 2 gated (its act NO, its craft 0), turns 1 and 3 kept: 6 of 9 judge elements score 1
    assert st["components"]["quality"]["share"] == pytest.approx(6 / 9)


# ------------------------------------------------------------------ a cut degenerate reply is a failure (lead 2026-11-14)

def _dgen_run(tmp_path, monkeypatch, rule, degenerate):
    """toychain (5 items, every one cut with nothing) + toychain2 (PASS); the cut row stamped
    degenerate or not."""
    it = {"family": "toychain", "level": 2, "count": 5, "items_per_prompt": 5, "ceiling": 2000}
    if rule:
        it["ran_out"] = rule
    prof = _flatprof([it, {"family": "toychain2", "level": 2, "count": 1}])
    _run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: ChatResult(
        response_text="", finish_reason="length", served_model="m", completion_tokens=9, prompt_tokens=20)
        if "toychain-" in cid or "-i" in cid else "PASS"})
    rows = config.load_transcripts("m")
    for r in rows:
        if r["chain"]["family"] == "toychain":
            r["degenerate"] = degenerate
    return prof, rows


@pytest.mark.parametrize("rule, degenerate, share, excluded", [
    ("exclude", True, 1 / 6, 0),    # a loop cut at the ceiling: its 5 items score 0, never dropped
    ("exclude", False, 1.0, 1),     # a real ran-out on an exclude cell: dropped, as before
    ("zero", True, 1 / 6, 0),       # zero cells unchanged
    ("zero", False, 1 / 6, 0)])
def test_a_cut_degenerate_reply_is_a_failure_on_the_board(tmp_path, monkeypatch, rule, degenerate, share, excluded):
    prof, rows = _dgen_run(tmp_path, monkeypatch, rule, degenerate)
    st = chains.section_stats(rows, prof)["work/main"]
    assert st["share"] == pytest.approx(share) and st["ran_out_excluded_units"] == excluded


@pytest.mark.parametrize("rule, degenerate, excluded", [("exclude", True, False), ("exclude", False, True),
                                                        ("zero", True, False)])
def test_a_cut_degenerate_reply_is_a_failure_on_the_scale(tmp_path, monkeypatch, rule, degenerate, excluded):
    from crucibleforge import scale
    _prof, rows = _dgen_run(tmp_path, monkeypatch, rule, degenerate)
    (u,) = [u for u in scale.units(rows) if u["cell"].startswith("toychain:")]
    assert bool(u.get("excluded")) is excluded
    if not excluded:
        assert u["x"] == 0.0
    rep = scale.unit_ran_out([u])[u["cell"]]
    assert rep["ran_out_units"] == (0 if degenerate else 1)        # never counted toward the 25%
    assert scale.ran_out_report([u])[u["cell"]]["ran_out"] == u["ran_out"] > 0   # still reported
    rr = scale.ran_out_report([u])[u["cell"]]
    assert rr["degenerate"] == int(degenerate)
    assert rr["ran_out_degenerate"] == (u["ran_out"] if degenerate else 0)
    assert rr["elements"] == 5                       # each cut element counted once, scored or dropped


def test_a_cut_degenerate_case_row_is_a_failure_on_the_scale():
    from crucibleforge import scale
    cal = {"cells": {"C1": {"b": 0.0, "section": "work"}}}
    base = {"case_id": "C1", "finish_reason": "length", "score": 0.0, "repeat": 1, "ts": "t"}
    (u,) = scale.case_units([{**base, "degenerate": True}], cal, ran_out="exclude")
    assert not u.get("excluded") and u["n"] == 1
    (u,) = scale.case_units([{**base, "response": "a real answer cut short"}], cal, ran_out="exclude")
    assert u.get("excluded")
