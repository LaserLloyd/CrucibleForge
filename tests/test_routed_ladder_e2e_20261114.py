"""A routed run end to end into the ladder phase (lead 2026-11-14): locator -> module -> the part's
pack gate -> pack cases run for the part that qualifies and not for the one that does not -> the pack
rows carry ``pack_gate`` and never enter the section scale -> the routed board shows the part's
ladder card. And a model routed low ends after its module with every pack closed, zero pack cases.

Toy families and the fake rig of test_chains_20261113 (no model, no rig, no network)."""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

from crucibleforge import chains, cli, config, ladder, profiles, report, routing

_spec = importlib.util.spec_from_file_location("_tc", Path(__file__).with_name("test_chains_20261113.py"))
_tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tc)
_toys = _tc._toys          # the toy families (autouse fixture of that module, used here too)


def _prof(tmp_path):
    prof = _tc._routed_prof(tmp_path)
    prof["chains"]["work/aux"] = {"flat": [{"family": "toychain", "level": 1, "count": 2, "name": "ax"}]}
    prof["scores"]["work"]["parts"]["aux"] = {"max": 5, "scoring": ["coding"], "cases": []}
    prof["ladder"] = {"unlock": 0.8}
    prof["cases"] = {"coding": []}
    prof["scores"].pop("chat", None)
    return prof


def _sets(tmp_path, monkeypatch):
    root = tmp_path / "ladder"
    for part, tag in (("main", "PKM"), ("aux", "PKA")):
        d = root / "work" / part
        d.mkdir(parents=True)
        cases = [{"id": f"{tag}-{i}", "category": "coding",
                  "generator": {"type": "toychain", "level": 2, "key": f"{tag.lower()}{i}"}} for i in (1, 2)]
        (d / "set-1.json").write_text(json.dumps({"level": 1, "points": 1, "cases": cases}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    return root


def _drive(tmp_path, monkeypatch, policies):
    prof = _prof(tmp_path)
    _sets(tmp_path, monkeypatch)
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})   # rows stamped "t"
    calls, summary = _tc._run_routed(tmp_path, monkeypatch, prof, policies)
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": m, "model_id": m, "provider": "p"} for m in policies]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    # no rig: the ladder round's resident guard has nothing to snapshot or restore
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    rc = cli._ladder_phases(args, cfg, list(policies))
    return prof, cfg, calls, summary, rc


def test_a_routed_run_reaches_the_ladder_phase_and_opens_only_the_maxed_part(tmp_path, monkeypatch):
    # clears Main (route: packs, whole) but fails the aux part: main maxed, aux not
    pol = lambda lv, cid: "nope" if "-ax-" in cid or cid.startswith("PKA") else "PASS"   # noqa: E731
    prof, cfg, calls, summary, rc = _drive(tmp_path, monkeypatch, {"m": pol})
    assert summary["m"]["routing"]["routed"] and [x["name"] for x in summary["m"]["routing"]["modules"]] == ["packs"]
    assert rc == 0
    rows = config.load_transcripts("m")
    pack = [r for r in rows if str(r.get("case_id", "")).startswith(("PKM", "PKA"))]
    assert {r["case_id"] for r in pack} == {"PKM-1", "PKM-2"}                 # aux's pack never ran
    g = pack[0]["pack_gate"]
    assert g["opened"] and g["part"] == "work/main" and g["target"] == "work/main" and g["share"] == 1.0
    assert g["threshold"] == 0.8 and g["unlock_from"] == "packs" and g["rule"] == "score"
    # never in the section scale
    assert not [r for r in report._without_pack_rows(prof, rows) if r.get("pack_gate")]
    # the routed board shows the part's ladder card: main's set scored, aux's locked with its reason
    prog = report.ladder_progress("m", cfg)
    assert prog["work/main"]["sets"] == {1: 1.0} and prog["work/main"]["pack_gate"]["opened"]
    assert prog["work/aux"]["sets"] == {1: None} and not prog["work/aux"]["pack_gate"]["opened"]
    assert prog["work/aux"]["pack_gate"]["reason"] == "share below the threshold"


def test_a_model_routed_low_ends_after_its_module_with_every_pack_closed(tmp_path, monkeypatch):
    pol = lambda lv, cid: "nope"   # noqa: E731
    prof, cfg, calls, summary, rc = _drive(tmp_path, monkeypatch, {"m": pol})
    assert [x["name"] for x in summary["m"]["routing"]["modules"]] == ["low"]
    rows = config.load_transcripts("m")
    assert not [r for r in rows if str(r.get("case_id", "")).startswith(("PKM", "PKA"))]   # zero pack cases
    assert not [c for c, _t in calls["m"] if c.startswith(("PKM", "PKA"))]
    prog = report.ladder_progress("m", cfg)
    for part in ("work/main", "work/aux"):
        assert prog[part]["pack_gate"]["opened"] is False
        assert prog[part]["pack_gate"]["reason"] == "route below packs"
