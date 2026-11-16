"""No 4.1 row counts on a 4.5 board (lead 2026-11-15, applied at the cut with the suite patch).

A 4.5 score comes only from rows made by the 4.5 run under 4.5 settings. bench45 declares
``reuse: {from: [bench], ids: [37 ids]}``: a byte-identical 4.1 row of one of those cases counted on the
board, in a Main part or a pack, and ``done_jobs`` (fed by the board rows) skipped the job, so the 4.5 run
never made its own row. The shipping composition (the profile's ``default_composition``, reached also
through an alias such as rc4-main) has no reuse: a composition may set ``reuse`` like ``cases``, the
shipping one is forced empty and a shipping composition that declares a non-empty reuse is refused.
Other compositions (rc3-main, calib-*) and 4.1 profiles are untouched.

The byte-identity checks of ``reuse_status`` (source tree, live views, measure view) are stubbed to "equal"
for the 4.1 row below, so only the profile's reuse declaration decides."""
from __future__ import annotations

import json

import pytest

from crucibleforge import cli, config, estimate, ladder, profiles, report, version
from crucibleforge.config import ConfigError

KEY = "work/agentic"
LAG1, LAG3 = "LAG1-potkeeper-yearend", "LAG3-tillroll-reconcile"
pytestmark = pytest.mark.live_ladder

SHIP = {"default_composition": "standard", "composition_aliases": {"rc4-main": "standard"}}


@pytest.fixture
def ship(monkeypatch, tmp_path):
    """bench45 as it will read at the cut: a ``standard`` composition (here the base set, so the classic
    agentic pack is in it) that is the profile's default, rc4-main an alias of it."""
    real = profiles._yaml_file

    def yaml_file(p):
        d = real(p)
        if getattr(p, "name", "") == "bench45.yaml" and isinstance(d, dict):
            d = {**d, **SHIP, "compositions": {**(d.get("compositions") or {}), "standard": {}}}
        return d
    monkeypatch.setattr(profiles, "_yaml_file", yaml_file)
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    monkeypatch.setattr(profiles, "_ACTIVE", ["bench45"])
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    report._MEASURE_VIEWS.clear()
    # the 4.1 rows below are byte-identical re-answers (stub: every tree view equal)
    monkeypatch.setattr(report, "_measure_view",
                        lambda rp: {k: profiles.load_profile("bench45").get(k) for k in report._MEASURE_KEYS})
    raw = {c: {"id": c} for c in (LAG1, LAG3)}
    monkeypatch.setattr(report, "_source_tree",
                        lambda rp: ({c: {"case": version.case_hash(c)} for c in raw}, raw))
    monkeypatch.setattr(version, "live_views", lambda: ({}, raw))
    yield tmp_path
    report._MEASURE_VIEWS.clear()


def _row(cid, profile, repeat=1):
    prof = profiles.load_profile("bench45")
    return {"bench_run_id": f"run-{profile}", "case_id": cid, "category": "codeagent_long", "repeat": repeat,
            "turn": None, "profile": profile, "regime": prof.get("regime"),
            "bench_revision": "4.1.0+3d9be8cc" if profile == "bench" else "4.5.0+aaaaaaaa",
            "case_revision": version.case_hash(cid), "grade": "pass", "score": 1.0, "finish_reason": "stop"}


def _store(folder, rows):
    (folder / "transcripts_m.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _pooled(cfg=None):
    """The rows of the classic agentic pack the board pools for model m."""
    return sorted((r["case_id"], r["profile"]) for r in report.board_filter("m", cfg, sampler_view="current")
                  if r.get("case_id") in (LAG1, LAG3))


def _todo(cfg):
    prof = profiles.load_profile("bench45", cfg)
    sets = {KEY: ladder.discover(prof)[KEY]}
    progress = {KEY: {"std": 1.0, "sets": {}}}
    cases, jobs, _ = cli.ladder_todo("m", cfg, prof, sets, 0.0, progress=progress)
    return {(cid, r) for (_, cid, r) in jobs if cid in (LAG1, LAG3)}, cases


def test_the_shipping_composition_reuses_nothing(ship):
    assert profiles.load_profile("bench45")["reuse"] == {}
    import os
    was = os.environ.get(profiles.COMPOSITION_ENV)
    os.environ[profiles.COMPOSITION_ENV] = "rc4-main"
    try:
        p = profiles.load_profile("bench45")
        assert p["composition"] == "standard" and p["reuse"] == {}
    finally:
        # restore, never delete: the session's base-composition pin must survive this test
        if was is None:
            os.environ.pop(profiles.COMPOSITION_ENV)
        else:
            os.environ[profiles.COMPOSITION_ENV] = was


def test_a_41_row_does_not_count_or_satisfy_a_job_on_the_45_board(ship):
    _store(ship, [_row(LAG1, "bench"), _row(LAG3, "bench45")])
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    config.RESULTS_DIR = ship
    cfg["_profile"] = "bench45"
    cfg["defaults"].setdefault("repeats", {})["codeagent_long"] = 2         # two samples per item
    assert _pooled(cfg) == [(LAG3, "bench45")]                      # n = 1: the 4.5 row only
    jobs, cases = _todo(cfg)
    reps = {c["id"]: config.repeats_for(cfg, c["category"], False, c) for c in cases if c["id"] in (LAG1, LAG3)}
    assert reps[LAG1] == 2 and reps[LAG3] == 2
    want = {(LAG1, r) for r in range(1, reps[LAG1] + 1)} | {(LAG3, r) for r in range(2, reps[LAG3] + 1)}
    assert jobs == want                                              # every LAG1 repeat is scheduled


def test_the_guard_is_not_vacuous_another_composition_still_reuses(ship, monkeypatch):
    """Under a composition that keeps the profile's reuse (``default`` = the base block), the same stub makes
    the 4.1 row count: the test above fails for the right reason when the rule is off."""
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")
    _store(ship, [_row(LAG1, "bench"), _row(LAG3, "bench45")])
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    config.RESULTS_DIR = ship
    cfg["_profile"] = "bench45"
    assert profiles.load_profile("bench45")["reuse"].get("ids")
    assert _pooled(cfg) == [(LAG1, "bench"), (LAG3, "bench45")]


def test_a_shipping_composition_that_declares_reuse_is_refused(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    prof = {"name": "t", "cases": {"x": ["a"]}, "reuse": {"from": ["bench"], "ids": ["a"]},
            "default_composition": "standard",
            "compositions": {"standard": {"reuse": {"from": ["bench"], "ids": ["a"]}}, "lab": {}}}
    with pytest.raises(ConfigError, match="reuse"):
        profiles.apply_composition(dict(prof), "t")
    # an explicit empty reuse is fine, and a non-shipping composition may still declare one
    ok = {**prof, "compositions": {"standard": {"reuse": {}}, "lab": {"reuse": {"from": ["bench"], "ids": ["b"]}}}}
    assert profiles.apply_composition(dict(ok), "t")["reuse"] == {}
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "lab")
    assert profiles.apply_composition(dict(ok), "t")["reuse"]["ids"] == ["b"]


def test_a_profile_without_a_shipping_composition_keeps_its_reuse(monkeypatch):
    """4.1 (bench) and bench45's base and non-shipping compositions: reuse stays exactly as declared (since the cut
    an unset composition is the shipping `standard`, so the base is asked for by name)."""
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")
    assert profiles.load_profile("bench45")["reuse"]["ids"]
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    assert not profiles.load_profile("bench").get("reuse")
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    assert len(profiles.load_profile("bench45")["reuse"]["ids"]) == 37


def test_pack_minutes_counts_the_set_stamp_over_case_repeats():
    """A set's ``repeats`` (stamped on its case dicts) wins over the profile's case_repeats, as in the
    runner (config.repeats_for reads the case's own ``repeats``)."""
    cs = [{"id": f"Q{i}", "category": "reasoning", "prompt": "x", "repeats": 2} for i in range(40)]
    base = {"repeats": {"reasoning": 1}}
    one = estimate.pack_minutes(base, [{**c, "repeats": 1} for c in cs])
    two = estimate.pack_minutes(base, cs)
    assert two > one                                                 # the repeat count shows in the estimate
    assert estimate.pack_minutes({**base, "case_repeats": {c["id"]: 5 for c in cs}}, cs) == two
