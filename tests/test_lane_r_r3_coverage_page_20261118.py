"""R3 (fix round 2026-11-18, AUDIT-HARNESS F7, REVIEW finding 2 label part): the coverage note counts only the page
being rendered. A model whose standard page is complete is not "partial" because its adult page did not run; a
standard page with a unit missing counts against the standard units only; an adult-only render counts the adult
units. Toy board: no model, no rig."""
from __future__ import annotations

from _lane_r_toy import PASS, board, row, toys  # noqa: F401


def _std_only(m, r):
    return not str((r.get("chain") or {}).get("part") or "").startswith("nsfw/")


def _adult_only(m, r):
    return str((r.get("chain") or {}).get("part") or "").startswith("nsfw/")


def test_a_complete_standard_page_is_not_partial(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS}, keep=_std_only)
    cov = stats["m"]["coverage"]
    assert cov["complete"] and cov["expected_cases"] == 12, cov
    assert "partial" not in row(md, "m")[-2]


def test_a_missing_standard_unit_counts_against_the_standard_units_only(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS},
                         keep=lambda m, r: _std_only(m, r) and (r.get("chain") or {}).get("id") != "FLAT-xa-001")
    assert "partial (11/12 cases)" in row(md, "m")[-2]


def test_an_adult_only_render_counts_the_adult_units(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS},
                         keep=lambda m, r: _adult_only(m, r) and (r.get("chain") or {}).get("id") != "FLAT-yn1-001")
    cov = stats["m"]["coverage"]
    assert cov["expected_cases"] == 16 and cov["cases"] == 15, cov
