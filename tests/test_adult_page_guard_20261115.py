"""The adult page never goes to a Claude Code row (lead 2026-11-15): models.yaml ``adult_page``, default off on the
Claude Code route (provider ``type: claude_code``), refused when set true there. The plan drops every adult unit for
such a model and says so in one line; an explicit adult ``--cases`` is refused before any call; the send path raises
whatever route an adult unit takes; the board reads "not run" and never ranks it. Nothing here sends anything:
there is no provider call in this file."""
from __future__ import annotations

import inspect
import types

import pytest

from crucibleforge import adult_guard as G
from crucibleforge import cli, config, profiles, runner
from crucibleforge.config import ConfigError

pytestmark = pytest.mark.live_ladder

PROVIDERS = {"cc": {"type": "claude_code"},
             "ds": {"type": "openai", "base_url": "https://api.example.invalid/v1", "api_key_env": "X"},
             "studioforge": {"type": "studioforge", "base_url": "http://rig.invalid:1234/v1"}}
CC = {"name": "cc-sonnet", "model_id": "sonnet", "provider": "cc"}
HOSTED = {"name": "deepseek-flash", "model_id": "deepseek-v4-flash", "provider": "ds"}
RIG = {"name": "qwen3.8-27b-q8-ref", "model_id": "pub/repo/file.gguf", "provider": "studioforge"}


def _cfg(*models):
    return {"providers": PROVIDERS, "models": [dict(m) for m in models], "judge": {"candidates": []},
            "defaults": {}}


@pytest.fixture(scope="module")
def std_cases():
    """The shipping composition's whole case list, as ``cmd_run`` builds it (no filters)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    prof = profiles.load_profile("bench45")
    profiles.activate_cases_root(prof)
    base = {"providers": PROVIDERS, "models": [], "judge": {"candidates": []},
            "defaults": {"max_tokens": 4096, "repeats": {}}}
    try:
        _cfg2, cases = profiles.apply_profile(prof, base)
    finally:
        mp.undo()
    return cases


# ------------------------------------------------------------------ the key
def test_default_is_off_on_the_claude_code_route_and_on_elsewhere():
    cfg = _cfg(CC, HOSTED, RIG)
    assert G.is_claude_code(cfg, CC) and not G.is_claude_code(cfg, HOSTED) and not G.is_claude_code(cfg, RIG)
    assert G.adult_page_on(cfg, CC) is False
    assert G.adult_page_on(cfg, HOSTED) is True and G.adult_page_on(cfg, RIG) is True
    assert G.adult_page_on(cfg, {**HOSTED, "adult_page": False}) is False
    assert G.adult_page_on(cfg, {**CC, "adult_page": False}) is False


def test_explicit_true_on_the_claude_code_route_is_refused_not_honoured():
    cfg = _cfg({**CC, "adult_page": True})
    with pytest.raises(ConfigError, match="refused on the Claude Code route"):
        config.validate_config(cfg)
    with pytest.raises(ConfigError, match="refused on the Claude Code route"):
        G.adult_page_on(cfg, {**CC, "adult_page": True})
    with pytest.raises(ConfigError, match="true or false"):
        config.validate_config(_cfg({**HOSTED, "adult_page": "yes"}))
    config.validate_config(_cfg(CC, HOSTED, RIG, {**RIG, "name": "r2", "adult_page": False}))


# ------------------------------------------------------------------ plan
def test_the_composition_has_adult_units(std_cases):
    adult = [c for c in std_cases if G.is_adult(c)]
    assert len(adult) >= 100, len(adult)                  # the 113 adult chains
    assert any(c.get("chain_spec") for c in adult)


def test_plan_for_a_claude_code_model_has_no_adult_unit(std_cases):
    said = []
    kept = G.plan_cases(_cfg(CC), CC, std_cases, say=said.append)
    assert not [c for c in kept if G.is_adult(c)]
    assert len(kept) == len([c for c in std_cases if not G.is_adult(c)])
    assert said == ["NSFW+: not run for cc-sonnet (nsfw_plus off for this model)"]


@pytest.mark.parametrize("entry", [HOSTED, RIG], ids=["hosted", "rig"])
def test_plan_for_a_hosted_or_rig_model_is_unchanged(std_cases, entry):
    # fix round 2026-11-18 (P1): the adult page is opt-in; with --adult (G.RUN_KEY) the plan is unchanged
    said = []
    kept = G.plan_cases({**_cfg(entry), G.RUN_KEY: True}, entry, std_cases, say=said.append)
    assert kept is std_cases and said == []
    assert [c["id"] for c in kept] == [c["id"] for c in std_cases]


def test_run_models_plans_through_the_guard(std_cases, monkeypatch):
    """runner.run_models hands each model's rounds only the cases plan_cases kept (no provider is called:
    plan_rounds is stubbed to record what it was given)."""
    seen = {}

    def fake_rounds(cfg, entry, cases, *a, **k):
        seen[entry["name"]] = [c["id"] for c in cases]
        return []
    monkeypatch.setattr(runner, "plan_rounds", fake_rounds)
    monkeypatch.setattr(runner, "provider_for", lambda cfg, e: types.SimpleNamespace(
        name=e["provider"], base_url="x", alive=lambda: True))
    monkeypatch.setattr(runner, "_merge_round_summaries", lambda parts: {})
    cfg = {**_cfg(CC, HOSTED, RIG), G.RUN_KEY: True}     # --adult (P1: opt-in since 2026-11-18)
    runner.run_models(cfg, [CC, HOSTED, RIG], std_cases)
    assert not [i for i in seen["cc-sonnet"] if i in {c["id"] for c in std_cases if G.is_adult(c)}]
    assert seen["deepseek-flash"] == [c["id"] for c in std_cases]
    assert seen["qwen3.8-27b-q8-ref"] == [c["id"] for c in std_cases]


def test_explicit_adult_case_for_a_claude_code_model_is_refused_before_any_call(std_cases, monkeypatch):
    adult_id = next(c["id"] for c in std_cases if G.is_adult(c))
    plain_id = next(c["id"] for c in std_cases if not G.is_adult(c) and c["category"] != "perf")
    cfg = _cfg(CC, HOSTED)
    calls = []
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, c: (cfg, list(std_cases), {}))
    monkeypatch.setattr(cli, "_fail_fast", lambda *a, **k: calls.append("fail_fast") or ([], {}))
    args = types.SimpleNamespace(models="cc-sonnet", categories=None, difficulty=None, cases=adult_id,
                                 smoke=False, fresh=False, force=False, cmd="run", allow_unpriced=True)
    with pytest.raises(SystemExit, match="refusing --cases"):
        cli.cmd_run(args, cfg)
    assert calls == []                              # refused before any check that could reach a provider
    # the same id for a hosted model goes on (stops at the stubbed fail_fast, which runs nothing)
    args.models = "deepseek-flash"
    assert cli.cmd_run(args, cfg) == 1 and calls == ["fail_fast"]
    # a non-adult id for the Claude Code model goes on too
    args.models, args.cases = "cc-sonnet", plain_id
    assert cli.cmd_run(args, cfg) == 1 and calls == ["fail_fast", "fail_fast"]


# ------------------------------------------------------------------ send path
def test_the_send_path_guard_raises_for_an_adult_unit_on_a_guarded_model(std_cases):
    cfg = _cfg(CC, HOSTED, {**RIG, "name": "rig-off", "adult_page": False})
    chain_unit = next(c for c in std_cases if G.is_adult(c) and c.get("chain_spec"))
    instance = {"id": "FLAT-a45_taboo-021-r1-a1", "category": "nsfw", "chain": {"part": "nsfw/n7_taboo"}}
    nocat = {"id": "x", "chain": {"part": "nsfw/u1_will"}}           # a chain instance without its category
    for case in (chain_unit, instance, nocat, {"id": "NC1", "category": "nsfw"}):
        for entry in (CC, {**RIG, "name": "rig-off", "adult_page": False}):
            with pytest.raises(G.AdultPageRefused):
                G.assert_sendable(cfg, entry, case)
        G.assert_sendable(cfg, HOSTED, case)        # a hosted model with the page on: no error
    G.assert_sendable(cfg, CC, {"id": "C1", "category": "coding"})


def test_every_generation_passes_the_send_path_guard():
    """The guard is the first statement of runner._run_case_repeat, the one function every generated row comes
    from (a fresh run, a resume, a chain's next attempt, recover, escalate and a routed stage all call it)."""
    src = inspect.getsource(runner._run_one_model_body)
    body = src[src.index("def _run_case_repeat("):]
    first = [ln.strip() for ln in body.splitlines()[1:] if ln.strip() and not ln.strip().startswith("#")][0]
    assert first == "_adult_guard.assert_sendable(cfg, entry, case)", first
    assert src.count("def _run_case_repeat(") == 1


# ------------------------------------------------------------------ board
def test_the_board_drops_a_guarded_models_adult_rows():
    rows = {1: [{"case_id": "a", "category": "nsfw", "chain": {"part": "nsfw/n7_taboo"}},
                {"case_id": "b", "category": "coding"}]}
    cfg = _cfg(CC, HOSTED)
    off = G.board_rows("cc-sonnet", cfg, {}, rows)
    assert [r["case_id"] for r in off[1]] == ["b"]
    assert G.board_rows("x", None, {"provider_type": "claude_code"}, rows)[1] == off[1]  # meta alone is enough
    assert G.board_rows("deepseek-flash", cfg, {}, rows) is rows


def test_a_claude_code_rows_adult_page_reads_not_run_and_is_never_ranked():
    """On the rc4 adult page: a Claude Code row with stored adult rows (the 2026-11-14 slip) scores nothing there."""
    from tests import test_scoring_rules_45 as T
    from crucibleforge import report
    mp = pytest.MonkeyPatch()
    mp.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    try:
        rows = T._rows(p)
        cfg = {"_profile": "bench45", "models": []}
        on = report.stats_from_tier_rows("toy", cfg, p, {1: rows}, {}, None)
        on["scorecard"] = report.tier_scorecard(on, report.scoring_config(cfg), p)
        off = report.stats_from_tier_rows("toy", cfg, p, {1: rows}, {"provider_type": "claude_code"}, None)
        off["scorecard"] = report.tier_scorecard(off, report.scoring_config(cfg), p)
    finally:
        mp.undo()
    assert on["scorecard"]["subtotals"]["nsfw"]["points"] is not None
    sub = off["scorecard"]["subtotals"]
    assert sub["nsfw"]["points"] is None and sub["uncensored"]["points"] is None and sub["total"]["points"] is None
    for k in ("nsfw", "uncensored", "total"):
        # in words, never a 0; 4.5.0 (fix round 2026-11-18, R4) withholds the NSFW number and the total either way
        want = "not run" if k == "uncensored" else report.ADULT_WITHHELD45
        assert report.subtotal_cell(off, k) == want, k
        assert report.subtotal_cell(on, k) != "not run", k
    assert off["scorecard"].get("nsfw") is None                    # no adult headline: never ranked there
    assert on["scorecard"].get("nsfw") is not None
