"""One honest margin on a non-routed points board (lead 2026-11-14, MiniMax rc2 board: "Coding 7.2 ± 0.2" over a
lower table at ± 0.85). The headline bootstrap drew only the profile's stored cases: a generated unit (FLAT-*) was in
no stratum, so every resample scored the same units and the generated parts read the 0.1 floor. Now every generated
set is a stratum of its own (units drawn with replacement, a unit drawn k times counts k times), so the headline's
part, section and Overall margins carry the units' spread, and a part that rests on one stored question declares
``question_sd`` (the question-to-question spread its repeats cannot measure), added in every resample.
Toy families, the fake rig (no model, no rig)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from crucibleforge import headline, report

_spec = importlib.util.spec_from_file_location("_rc2", Path(__file__).with_name("test_rc2_main_20261114.py"))
_rc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rc2)
_toys = _rc2._toys

#: half the units of each part pass: a real spread to measure
HALF = lambda lv, cid: "PASS" if int(cid.split("-")[2]) % 2 else "nope"   # noqa: E731


def _stats(tmp_path, monkeypatch, pol, **part_extra):
    orig = _rc2._toy_prof

    def prof():
        p = orig()
        p["scores"]["work"]["parts"]["main"].update(part_extra)
        return p
    monkeypatch.setattr(_rc2, "_toy_prof", prof)
    cfg, _ = _rc2._drive(tmp_path, monkeypatch, pol)
    _, stats, _ = report.board_stats("m", cfg)
    return stats["m"]


def test_generated_units_are_strata_of_the_headline_bootstrap(tmp_path, monkeypatch):
    s = _stats(tmp_path, monkeypatch, HALF)
    prof = _rc2._toy_prof()
    st = headline.strata(prof, report.scoring_config(None), None)
    units = {k: v for k, v in st.items() if str(k[1]).startswith("work/main@units")}
    assert sorted(i for v in units.values() for i in v) == [f"FLAT-mn-{i:03d}" for i in range(1, 11)]
    hl, cs = s["headline"], s["chain_stats"]
    # the headline margin of each generated part is the units' spread (bootstrap vs the lower table's
    # analytic SE: within sampling error of a 10-unit bootstrap), never the 0.1 floor
    for part in ("main", "aux"):
        m, m90 = hl["m"][f"work/{part}"], cs[f"work/{part}"]["margin_90"]
        assert m > 0.3 and 0.7 * m90 <= m <= 1.3 * m90, (part, m, m90)
    sec = (cs["work/main"]["se_points"] ** 2 + cs["work/aux"]["se_points"] ** 2) ** 0.5
    # the section is scaled 10/8 (judged part pending): its margin scales with it
    assert 0.7 * 1.645 * sec * 10 / 8 <= hl["m"]["work"] <= 1.3 * 1.645 * sec * 10 / 8
    assert hl["m"]["overall"] == pytest.approx(hl["m"]["work"], abs=0.11)


def test_a_part_declares_its_question_spread(tmp_path, monkeypatch):
    base = _stats(tmp_path / "a", monkeypatch, HALF)["headline"]["m"]["work/main"]
    s = _stats(tmp_path / "b", monkeypatch, HALF, question_sd=0.3)
    m = s["headline"]["m"]["work/main"]
    want = (base ** 2 + (1.645 * 0.3 * 5) ** 2) ** 0.5
    assert 0.85 * want <= m <= 1.15 * want, (m, want)
    assert s["headline"]["m"]["work"] > base
    # the point estimate never moves
    assert s["headline"]["pts"]["work/main"] == _stats(tmp_path / "c", monkeypatch, HALF)["headline"]["pts"]["work/main"]


def test_the_board_prints_one_margin_per_part_and_section(tmp_path, monkeypatch):
    s = _stats(tmp_path, monkeypatch, HALF)
    hl = s["headline"]
    cfg = _rc2._tc._cfg(_rc2._toy_prof())
    md = report.generate("m", write=False, cfg={**cfg, "models": [{"name": "m", "model_id": "m", "provider": "p"}]})
    # the part rows of the lower table carry the Scorecard's margin for that part, nothing else
    for part in ("main", "aux"):
        row = next(l for l in md.splitlines() if l.startswith(f"| m | work/{part} |"))
        assert row.split(" | ")[8] == f"{hl['m'][f'work/{part}']:.1f}", row
        assert f"{part.capitalize()} {hl['pts'][f'work/{part}']:.1f}±{hl['m'][f'work/{part}']:.1f}" in md
    # no second, generated-parts-only section / Overall margin
    assert "Score (generated parts; Overall = all)" not in md
    assert "Margin = the Scorecard's ± for that part" in md
    assert f"± {hl['m']['work']:.1f}" in md
