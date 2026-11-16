"""The bonus packs on a routed board, end to end (lead 2026-11-14): K's toy routed run
(test_routed_ladder_e2e_20261114) where work/main opens its pack and work/aux stays locked,
rendered through report.generate: the Scorecard cell, the section row and the Bonus packs lines.
The locked line carries the run's own pack_gate reason. Toy families, no model, no rig."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from crucibleforge import report

_spec = importlib.util.spec_from_file_location(
    "_e2e", Path(__file__).with_name("test_routed_ladder_e2e_20261114.py"))
_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2e)
_toys = _e2e._toys


def _board(tmp_path, monkeypatch, pol):
    prof, cfg, calls, summary, rc = _e2e._drive(tmp_path, monkeypatch, {"m": pol})
    assert rc == 0
    md = report.generate("m", write=False, cfg=cfg)
    _, stats, _ = report.board_stats("m", cfg)
    return md, stats


def test_the_opened_part_scores_its_pack_the_locked_part_says_the_gate(tmp_path, monkeypatch):
    # K's toy: Main cleared (route: packs), work/main maxed, work/aux failed
    pol = lambda lv, cid: "nope" if "-ax-" in cid or cid.startswith("PKA") else "PASS"   # noqa: E731
    md, stats = _board(tmp_path, monkeypatch, pol)
    hl = stats["m"]["headline"]
    # the Scorecard: standard + bonus on the published form, the max = 10 + one per pack
    assert "| Overall (/12 = 10 standard + 2 packs) | Class | Lean | Work (/12 = 10 standard + 2 packs) |" in md
    std = hl["std45"]["work"]
    assert hl["bonus45"]["work"] == 1.0 and hl["S"]["work"] == round(std + 1.0, 1)
    assert f"/12 = {std:.1f} + 1.0 bonus" in md
    # the section row
    assert f"| m | Work | {hl['S']['work']:.1f} / 12 (= {std:.1f} standard + 1.0 bonus) |" in md
    # the pack lines: main scored on its 2 units; aux locked with the run's own gate reason
    assert "| m | work/main | pack 1 (level 1) | scored | 1.00 / 1 | 2 | 0.00 |" in md
    assert "| m | work/aux | pack 1 (level 1) | locked: share below the threshold (0% < 80%) | - | - | - |" in md
    # Class from Overall incl. bonus (8.2: C), no Lean without Chat; work/main MAXED (100% + its pack)
    assert f"| 1 | m | {report.headline_cell(hl, 'overall')} /12 = {std:.1f} + 1.0 bonus | C | - |" in md
    assert hl["maxed"]["work"] == {"parts": ["main"], "category": False}
    assert "Main 11.0±0.1 (set 1) MAXED + Aux" in md
    # pack rows never in the section scale: the standard section equals the scale's own points
    assert std == round(stats["m"]["scale45"]["sections"]["work"]["points"] / 10, 1)


def test_a_model_routed_low_has_every_pack_locked_by_its_route(tmp_path, monkeypatch):
    md, stats = _board(tmp_path, monkeypatch, lambda lv, cid: "nope")
    hl = stats["m"]["headline"]
    assert (hl.get("bonus45") or {}).get("work", 0.0) == 0.0
    assert "| m | work/main | pack 1 (level 1) | locked: route below packs | - | - | - |" in md
    assert "| m | work/aux | pack 1 (level 1) | locked: route below packs | - | - | - |" in md
