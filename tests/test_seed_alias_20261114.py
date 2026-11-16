"""The ``seed_case`` alias (lead 2026-11-14): a ladder pack case whose generator names a measured
flat chain instance (``seed_case`` = ``FLAT-<set>-<n>-r<r>-a<a>``) takes that stored row instead of
re-running it, graded by the same generator. Guards: the stored row is current under the tree's
stamps; its rebuilt instance sends what it sent; the pack case's materialised instance equals it
byte for byte; a row of the board profile's own chain composition is never aliased; an aliased row
counts once, as the pack's row, never in the section scale.

Toy families and the routed toy run of test_chains_20261113 / test_routed_ladder_e2e_20261114."""
from __future__ import annotations

import copy
import importlib.util
import json
import types
from pathlib import Path

import pytest

from crucibleforge import chains, cli, config, ladder, profiles, report

_spec = importlib.util.spec_from_file_location("_tc", Path(__file__).with_name("test_chains_20261113.py"))
_tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tc)
_toys = _tc._toys

_e2e_spec = importlib.util.spec_from_file_location("_e2e", Path(__file__).with_name("test_routed_ladder_e2e_20261114.py"))
_e2e = importlib.util.module_from_spec(_e2e_spec)
_e2e_spec.loader.exec_module(_e2e)


def _seed_prof():
    """The composition the pack seeds were measured under (not the board's)."""
    return _tc._flatprof([{"family": "toychain", "level": 1, "count": 2, "name": "sx"}])


@pytest.fixture(autouse=True)
def _fresh():
    report._SEED_ALIAS_CACHE.clear()
    report._ALIAS_REFUSAL_CACHE.clear()
    yield
    report._SEED_ALIAS_CACHE.clear()
    report._ALIAS_REFUSAL_CACHE.clear()


def _stored_rows(template: dict, answer: str = "PASS") -> list[dict]:
    """Stored rows of the seed set ``sx`` as a run writes them: the run's own stamps (from
    ``template``, a real row of the toy run), the instance's prompt and chain block."""
    out = []
    for c in chains.chain_cases(_seed_prof()):
        inst = chains.instance(c, 1, 1)
        case = chains.materialize(inst)
        r = copy.deepcopy(template)
        r.update(case_id=inst["id"], prompt=case["prompt"], system=case.get("system", ""), response=answer,
                 chain={**inst["chain"], "score": 1.0, "passed": True, "outcome": "pass"},
                 chain_revision=chains.revision(inst["chain"]), grade="pass", score=1.0, repeat=1)
        for k in ("route", "stage", "pack_gate", "seed_alias", "elements"):
            r.pop(k, None)
        out.append(r)
    return out


def _drive(tmp_path, monkeypatch, mutate=None):
    """The routed toy run (Main maxed: both parts open), then the stored seed rows appended,
    then the ladder phase. The aux part's pack set = ladder_seed_cases of ``sx``."""
    prof = _e2e._prof(tmp_path)
    prof["missing_rows"] = "pending"        # bench45's rule: a set with a job missing is incomplete, not scored
    root = _e2e._sets(tmp_path, monkeypatch)
    seed = chains.ladder_seed_cases(_seed_prof(), "sx", "PKA")
    assert [c["generator"]["seed_case"] for c in seed] == ["FLAT-sx-001-r1-a1", "FLAT-sx-002-r1-a1"]
    (root / "work" / "aux" / "set-1.json").write_text(
        json.dumps({"level": 1, "points": 1, "cases": seed}), encoding="utf-8")
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    calls, _summary = _tc._run_routed(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "PASS"})
    rows = config.load_transcripts("m")
    template = next(r for r in rows if r.get("chain"))
    stored = _stored_rows(template)
    if mutate:
        mutate(stored)
    path = next(Path(tmp_path).rglob("transcripts_m.jsonl"))
    with path.open("a", encoding="utf-8") as fh:
        for r in stored:
            fh.write(json.dumps(r) + "\n")
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": "m", "model_id": "m", "provider": "p"}]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    n0 = len(calls["m"])
    rc = cli._ladder_phases(args, cfg, ["m"])
    return prof, cfg, [c for c, _t in calls["m"][n0:]], stored, rc


def test_a_seeded_pack_takes_the_stored_rows_and_never_reruns_them(tmp_path, monkeypatch):
    prof, cfg, ran, stored, rc = _drive(tmp_path, monkeypatch)
    assert rc == 0
    # PKA never ran (a PKA case sends its seed's prompt, so the rig sees the FLAT id: none here)
    assert sorted(ran) == ["PKM-1", "PKM-2"]
    board = report.board_filter("m", cfg, sampler_view="current")
    pka = sorted((r for r in board if str(r["case_id"]).startswith("PKA")), key=lambda r: r["case_id"])
    assert [r["case_id"] for r in pka] == ["PKA-01", "PKA-02"]
    assert [r["seed_alias"]["row_case_id"] for r in pka] == ["FLAT-sx-001-r1-a1", "FLAT-sx-002-r1-a1"]
    assert all("chain" not in r and r["grade"] == "pass" and r["score"] == 1.0 for r in pka)   # verdict unchanged
    assert not [r for r in board if str(r["case_id"]).startswith("FLAT-sx")]       # counted once, as the pack
    # done for the ladder: a resumed phase asks for nothing more
    assert {("PKA-01", 1), ("PKA-02", 1)} <= cli.done_jobs(board)
    # never in the section scale
    assert not [r for r in report._without_pack_rows(prof, board) if str(r["case_id"]).startswith(("PKA", "FLAT-sx"))]
    # the aux pack scores from them on the ladder card
    assert report.ladder_progress("m", cfg)["work/aux"]["sets"] == {1: 1.0}


@pytest.mark.parametrize("what, mutate", [
    ("prompt", lambda rows: rows[0].update(prompt=rows[0]["prompt"] + " (an older wording)")),
    ("stale", lambda rows: rows[0].update(chain_revision="00000000")),
    # a block whose key is not this release's (restamped, so only the rebuild guard can catch it)
    ("key", lambda rows: (rows[0]["chain"].update(seed_id="0" * 16),
                          rows[0].update(chain_revision=chains.revision(rows[0]["chain"])))),
])
def test_a_row_that_fails_a_guard_is_refused_and_the_pack_case_runs(tmp_path, monkeypatch, what, mutate):
    prof, cfg, ran, stored, rc = _drive(tmp_path, monkeypatch, mutate)
    assert rc == 0
    # only the refused one re-runs (it sends its seed's prompt: the rig sees the FLAT id)
    assert sorted(ran) == ["FLAT-sx-001-r1-a1", "PKM-1", "PKM-2"]
    lc = report.seed_aliases(prof)["FLAT-sx-001-r1-a1"]
    why = report.alias_refusal(stored[0], lc)
    assert why and {"prompt": "did not send", "stale": "not current", "key": "cannot be rebuilt"}[what] in why
    board = report.board_filter("m", cfg, sampler_view="current")
    one = [r for r in board if r["case_id"] == "PKA-01"]
    assert len(one) == 1 and "seed_alias" not in one[0]        # the fresh run's row, not the stored one


def test_a_pack_case_whose_instance_differs_is_refused(tmp_path, monkeypatch):
    prof, _cfg, _ran, stored, _rc = _drive(tmp_path, monkeypatch)
    lc = copy.deepcopy(report.seed_aliases(prof)["FLAT-sx-001-r1-a1"])
    row = stored[0]
    assert report.alias_refusal(row, lc) is None
    lc["generator"]["level"] = 2                               # another generator: a different instance
    report._ALIAS_REFUSAL_CACHE.clear()
    assert "differs" in report.alias_refusal(row, lc)


def test_a_main_unit_of_the_board_profile_is_never_aliased(tmp_path, monkeypatch):
    # a pack seeded on one of Main's own units would count one answer twice: it stays Main's
    prof = _e2e._prof(tmp_path)
    main_rows = [chains.instance(c, 1, 1) for c in chains.chain_cases(prof)
                 if (c.get("chain_spec") or c).get("ladder") == "ax"]
    lc = {"id": "PKA-01", "category": "coding"}
    monkeypatch.setattr(report, "seed_aliases", lambda p: {main_rows[0]["id"]: lc})
    r = {"case_id": main_rows[0]["id"], "chain": main_rows[0]["chain"]}
    out = report._with_seed_aliases([r], prof, chains.profile_index(prof))
    assert out == [r]
    assert report._without_pack_rows(prof, [r]) == [r]          # and it stays in the scale

