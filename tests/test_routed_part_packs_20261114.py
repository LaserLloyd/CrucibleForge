"""Routed part packs on the ladder path (the owner's pack rule, 2026-11-14): a part's bonus pack opens
when the model has maxed that part's own segment of Main, by score, behind the route's
``unlock_from`` module; each pack row records what its gate compared. One mechanism: the 4.x
ladder (set files, ladder phase, ladder.unlock, ladder points)."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import cli, config, ladder, profiles, report, routing, runner

TOP = {"modules": [{"name": "top", "share": "all"}], "unlock": True}
LOW = {"modules": [{"name": "low", "share": "all"}], "unlock": False}
MIDTOP = {"modules": [{"name": "mid", "share": "half"}, {"name": "top", "share": "half"}], "unlock": False}


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "main-routed")
    return profiles.load_profile("bench45")


def _parts(prof):
    """Two standard parts of the routed profile (any two with max > 0)."""
    out = []
    for sec, sv in prof["scores"].items():
        for pk, ps in (sv.get("parts") or {}).items():
            if float((ps or {}).get("max", 0) or 0) > 0:
                out.append(f"{sec}/{pk}")
    assert len(out) >= 2
    return out[:2]


def _gate(prof, key, share, route):
    return report._routed_pack_gate(prof, key, {"key": key.split("/")[1]}, share, ladder.unlock_share(prof),
                                    route)


def _todo(monkeypatch, prof, progress, have=()):
    """cli.ladder_todo over one one-set pack per part, with the given progress (no files, no rows)."""
    sets = {k: [{"n": 1, "level": 1, "points": 1, "cases": [f"{k}-c1", f"{k}-c2"]}] for k in progress}
    monkeypatch.setattr(ladder, "set_cases", lambda prof, key, n, smoke=False, sets=None, rungs=None:
                        [{"id": f"{key}-c{i}", "category": "coding", "ladder": f"{key}#{n}"} for i in (1, 2)])
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, sampler_view=None:
                        [{"case_id": c, "repeat": 1} for c in have])
    monkeypatch.setattr(config, "repeats_for", lambda cfg, cat, smoke=False, c=None: 1)
    return cli.ladder_todo("m", {}, prof, sets, ladder.unlock_share(prof), progress=progress)


def _progress(gate, opened, rec):
    return {"std": gate, "opened": opened, "sets": {}, "rungs": {}, "pack_gate": rec}


def test_a_maxed_part_opens_its_own_pack_only(monkeypatch, prof):
    a, b = _parts(prof)
    ga, oa, ra = _gate(prof, a, 0.95, TOP)
    gb, ob, rb = _gate(prof, b, 0.5, TOP)
    assert ra["opened"] and not rb["opened"] and rb["reason"] == "share below the threshold"
    assert ra["threshold"] == ladder.unlock_share(prof) and ra["rule"] == "score" and ra["margin"] is None
    cases, jobs, names = _todo(monkeypatch, prof, {a: _progress(ga, oa, ra), b: _progress(gb, ob, rb)})
    assert names == [f"{a}#1"] and {c["id"] for c in cases} == {f"{a}-c1", f"{a}-c2"}


def test_below_the_threshold_no_pack_case_runs(monkeypatch, prof):
    a, b = _parts(prof)
    prog = {k: _progress(*_gate(prof, k, ladder.unlock_share(prof) - 0.01, TOP)) for k in (a, b)}
    cases, jobs, names = _todo(monkeypatch, prof, prog)
    assert len(cases) == 0 and len(jobs) == 0 and names == []


def test_routed_below_unlock_from_opens_nothing_at_any_share(monkeypatch, prof):
    a, _b = _parts(prof)
    g, o, rec = _gate(prof, a, 0.95, LOW)
    assert g is None and o is False and rec == {**rec, "opened": False, "reason": "route below top",
                                                "unlock_from": "top"}
    assert _todo(monkeypatch, prof, {a: _progress(g, o, rec)})[0] == []
    # the same share with a route that includes unlock_from (a straddle half counts) opens it
    g, o, rec = _gate(prof, a, 0.95, MIDTOP)
    assert rec["opened"] and len(_todo(monkeypatch, prof, {a: _progress(g, o, rec)})[0]) == 2
    # a part that says unlock_from: mid opens on a mid route; no route yet opens nothing
    p2 = copy.deepcopy(prof)
    sec, pk = a.split("/")
    p2["scores"][sec]["parts"][pk]["unlock_from"] = "mid"
    assert _gate(p2, a, 0.95, {"modules": [{"name": "mid", "share": "all"}]})[2]["opened"]
    assert _gate(prof, a, 0.95, None)[2]["reason"] == "no route yet"


def test_each_pack_row_records_its_gate(monkeypatch, prof):
    a, _b = _parts(prof)
    g, o, rec = _gate(prof, a, 0.9, TOP)
    cases, _jobs, _names = _todo(monkeypatch, prof, {a: _progress(g, o, rec)})
    assert cases and all(c["pack_gate"] == {**rec, "part": a} for c in cases)
    # the runner copies it onto the row
    src = open(runner.__file__, encoding="utf-8").read()
    assert 'extra["pack_gate"] = copy.deepcopy(case["pack_gate"])' in src
    assert set(rec) >= {"target", "share", "threshold", "opened", "reason", "unlock_from", "margin"}


def test_resume_skips_finished_pack_units(monkeypatch, prof):
    a, _b = _parts(prof)
    g, o, rec = _gate(prof, a, 0.9, TOP)
    cases, jobs, _ = _todo(monkeypatch, prof, {a: _progress(g, o, rec)}, have=[f"{a}-c1"])
    assert len(cases) == 2 and jobs == {(None, f"{a}-c2", 1)}


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_a_pack_set_never_runs_in_the_locator_or_a_module(prof, monkeypatch):
    # main-routed names calibration-g2-1, which lacks three of its cells (the load-time check
    # refuses a run of it; test_ran_out_scoring_profile_20261114): this test is about staging only
    monkeypatch.setattr(routing, "check_calibration", lambda *a, **k: None)
    ids = ladder.all_ladder_case_ids(prof)
    assert ids, "the routed composition has ladder pack sets"
    sp = routing.spec(prof)
    # the run's case list = the profile's selected cases + its chain units (cli: apply_profile)
    _cfg, run_cases = profiles.apply_profile(prof, {"defaults": {}})
    staged = {c["id"] for c in routing.stage_cases(run_cases, sp)}
    for m in sp["modules"]:
        staged |= {c["id"] for c in routing.stage_cases(run_cases, sp, {"modules": [{"name": m["name"],
                                                                                    "share": "all"}]})}
    assert staged and not (ids & staged)


def test_pack_rows_never_enter_the_section_scale(prof):
    ids = sorted(ladder.all_ladder_case_ids(prof)) or ["X-pack"]
    rows = [{"case_id": ids[0], "repeat": 1, "score": 1.0},
            {"case_id": "STD-1", "repeat": 1, "score": 1.0, "pack_gate": {"opened": True}},
            {"case_id": "STD-2", "repeat": 1, "score": 1.0},
            {"chain": {"id": "FLAT-x-001"}, "case_id": "FLAT-x-001-r1-a1"}]
    kept = report._without_pack_rows(prof, rows)
    assert [r["case_id"] for r in kept] == (["STD-2", "FLAT-x-001-r1-a1"] if ids[0] != "X-pack"
                                            else ["X-pack", "STD-2", "FLAT-x-001-r1-a1"])


def test_the_routed_top_module_packs_path_is_untouched():
    # a part named in the top module's ``packs:`` keeps the route's decision (no share, no pack_gate)
    src = open(report.__file__, encoding="utf-8").read()
    i = src.index("if pkey in routed_packs:\n                gate, opened = None, route_open")
    j = src.index("elif is_routed:\n                gate, opened, pack_gate = _routed_pack_gate")
    assert i < j


def test_unlock_from_names_a_module_of_the_routing(prof):
    from crucibleforge.config import ConfigError
    a, _b = _parts(prof)
    sec, pk = a.split("/")
    p2 = copy.deepcopy(prof)
    p2["scores"][sec]["parts"][pk]["unlock_from"] = "mid"
    routing.spec(p2)
    p2["scores"][sec]["parts"][pk]["unlock_from"] = "nosuch"
    with pytest.raises(ConfigError, match="unlock_from: no module 'nosuch'"):
        routing.spec(p2)


def _with_from(prof, key, frm):
    p2 = copy.deepcopy(prof)
    sec, pk = key.split("/")
    if frm is None:
        p2["scores"][sec]["parts"][pk].pop("unlock_from", None)
    else:
        p2["scores"][sec]["parts"][pk]["unlock_from"] = frm
    return p2


def _r(*names):
    return {"modules": [{"name": n, "share": "all" if len(names) == 1 else "half"} for n in names]}


@pytest.mark.parametrize("frm,route,opens", [
    # unlock_from: mid -> at or above mid, a straddle counts as its higher half
    ("mid", _r("mid"), True), ("mid", _r("top"), True), ("mid", _r("low", "mid"), True),
    ("mid", _r("mid", "top"), True), ("mid", _r("low"), False),
    # unlock_from: low -> every route
    ("low", _r("low"), True), ("low", _r("mid"), True), ("low", _r("top"), True),
    ("low", _r("low", "mid"), True), ("low", _r("mid", "top"), True),
    # default (top): top whole, or a mid/top straddle (its higher half); never mid or low
    (None, _r("top"), True), (None, _r("mid", "top"), True), (None, _r("mid"), False),
    (None, _r("low"), False), (None, _r("low", "mid"), False),
])
def test_unlock_from_is_ordinal_in_the_module_order(prof, frm, route, opens):
    assert [m["name"] for m in routing.spec(prof)["modules"]] == ["low", "mid", "top"]
    a, _b = _parts(prof)
    g, o, rec = _gate(_with_from(prof, a, frm), a, 0.95, route)
    assert rec["opened"] is opens and rec["unlock_from"] == (frm or "top")
    if not opens:
        assert g is None and o is False and rec["reason"] == f"route below {frm or 'top'}"
