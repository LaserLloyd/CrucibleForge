"""Adult pack gate (ship plan C8, lead 2026-11-15): a pack that unlocks on its score (``unlock_part``) read the
score's component share, which renormalises over what is measured, so Lockout alone opened Grok's adult packs
while Sext / Anat / Know were unjudged. Every Main part of the owning score (max > 0) must have its points;
otherwise the gate is None, the pack stays locked (on the board and on the run side, ladder_progress) and the
board says why. Toy families, no model."""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

from crucibleforge import cli, config, ladder, profiles, report, routing

_spec = importlib.util.spec_from_file_location("_rc2", Path(__file__).with_name("test_rc2_main_20261114.py"))
_rc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rc2)
_tc = _rc2._tc
_toys = _tc._toys          # the toy families (autouse fixture of that module, used here too)


def _prof(judged: bool):
    prof = _tc._flatprof([{"family": "toychain", "level": 1, "count": 10, "name": "mn"}])
    prof["scores"]["work"]["parts"]["main"]["max"] = 5
    if judged:
        prof["scores"]["work"]["parts"]["judged"] = {"label": "Judged", "max": 2, "scoring": ["rpdeep"], "cases": []}
    prof["scores"]["work"]["parts"]["frontier"] = {"label": "Frontier", "max": 0, "scoring": ["coding"],
                                                   "unlock_part": "work"}
    prof["scores"]["work"]["label"] = "Coding"
    prof["ladder"] = {"unlock": 0.9}
    prof["cases"] = {"coding": []}
    prof["scores"].pop("chat", None)
    return prof


def _drive(tmp_path, monkeypatch, judged):
    prof = _prof(judged)
    assert routing.spec(prof) is None
    root = tmp_path / "ladder"
    d = root / "work" / "frontier"
    d.mkdir(parents=True)
    cases = [{"id": f"PKF-{i}", "category": "coding",
              "generator": {"type": "toychain", "level": 2, "key": f"pkf{i}"}} for i in (1, 2)]
    (d / "set-1.json").write_text(json.dumps({"level": 1, "points": 1, "cases": cases}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    _tc._run(tmp_path, monkeypatch, prof, {"m": lambda lv, cid: "PASS"})
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": "m", "model_id": "m", "provider": "p"}]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    assert cli._ladder_phases(args, cfg, ["m"]) == 0
    return cfg, {r["case_id"] for r in config.load_transcripts("m")}


def test_a_pending_main_part_keeps_the_score_s_pack_locked(tmp_path, monkeypatch):
    cfg, ran = _drive(tmp_path, monkeypatch, judged=True)
    assert not {"PKF-1", "PKF-2"} & ran                    # the run side: never sent
    det = report.ladder_progress("m", cfg)["work/frontier"]
    assert det["std"] is None
    md = report.generate("m", write=False, cfg=cfg)
    assert "| m | locked (Coding not fully scored: Judged pending) |" in md, md


def test_every_main_part_scored_opens_the_pack(tmp_path, monkeypatch):
    cfg, ran = _drive(tmp_path, monkeypatch, judged=False)
    assert {"PKF-1", "PKF-2"} <= ran
    md = report.generate("m", write=False, cfg=cfg)
    assert "| m | 100% |" in md, md
