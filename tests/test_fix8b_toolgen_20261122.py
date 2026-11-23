"""Uses tools: the seeded tool tasks (chain part work/c5_toolgen, max 0 in the score tree, 1 of Tools' 2 points in
segments45) reach the Tools segment with their own share, and their run range counts their own units' repeats."""
from __future__ import annotations

import pytest

from crucibleforge import chains, headline, profiles, report
from crucibleforge import segments45 as S

PART = "work/c5_toolgen"
pytestmark = pytest.mark.shipping_default


def _rows(prof):
    """One row per seeded tool unit; units 0, 2, 4, 6, 8 (four checks each) fail, every other unit passes.
    Unit mean 25/30, element mean 70/90: the part averages per unit (average: unit)."""
    units = [c for c in chains.chain_cases(prof) if c["chain_spec"]["part"] == PART]
    assert len(units) == 30
    rows = []
    for i, c in enumerate(units):
        sp = c["chain_spec"]
        n_el = 4 if i % 2 == 0 else 2
        s = 0.0 if (i % 2 == 0 and i < 10) else 1.0
        els = [{"id": f"{c['id']}-c{k}", "check": f"c{k}", "score": s, "weight": 1.0, "source": "code",
                "outcome": "pass" if s else "fail"} for k in range(n_el)]
        rows.append({"case_id": f"{c['id']}-r1-a1", "repeat": 1, "model_label": "m", "score": s,
                     "chain": {"id": c["id"], "kind": "flat", "part": PART, "ladder": sp["ladder"], "c": sp["c"],
                               "rung": 1, "attempt": 1, "K": 1, "element": "check", "ran_out": "exclude",
                               "score": s, "passed": bool(s), "outcome": "pass" if s else "fail", "elements": els}})
    return rows


def test_max0_part_keeps_its_unit_share():
    prof = profiles.load_profile("bench45")
    st = chains.section_stats(_rows(prof), prof)[PART]
    assert st["max"] == 0 and st["points"] == 0           # no points in the score tree (segments45 gives them)
    assert st["complete"] and st["units"] == st["ended"] == 30
    assert st["mean"] == pytest.approx(70 / 90)
    assert st["share"] == pytest.approx(25 / 30)          # per unit first, as the set's average says


def test_tools_segment_completes_from_the_chain_share():
    prof = profiles.load_profile("bench45")
    st = chains.section_stats(_rows(prof), prof)[PART]
    m = {"headline": {"max": {"work/c5_tools": 2.5, PART: 0.0}, "P": {}, "m": {}},
         "chain_stats": {PART: st},
         "scorecard": {"ladder": {"work": {"c5_tools": {"std_share": 0.7}}}},
         "rpdeep": {"per_case": {}}, "assistant45": {}}
    tools = S.segment_table(m, prof)["code"]["segments"]["tools"]
    units = {u["id"]: u for u in tools["units"]}
    assert units["c5_toolgen"]["share"] == pytest.approx(25 / 30, abs=1e-4)
    assert not tools["pending"] and tools["complete"]
    assert tools["points"] == pytest.approx(0.7 + 25 / 30, abs=1e-3)   # 1 point each, Tools max 2
    assert tools["max"] == 2


def test_run_range_counts_the_units_own_repeats(monkeypatch):
    prof = profiles.load_profile("bench45")
    rows = _rows(prof)
    rows += [{**r, "repeat": 2} for r in rows[:3]]          # three units ran twice
    ids = sorted(r["chain"]["id"] for r in rows)
    monkeypatch.setattr(headline, "strata", lambda *a, **k: {(1, f"{PART}@units"): sorted(set(ids))})
    monkeypatch.setattr(report, "_parallel_boots", lambda jobs: [{"samples": {}} for _ in jobs])
    monkeypatch.setattr(report, "tier_rows", lambda prof, rows, meta: rows)
    monkeypatch.setattr(report, "scoring_config", lambda cfg: {})
    monkeypatch.setattr(report, "_profile_case_ids", lambda cfg, with_tiers=False: set())
    out = report.repeat_noise45(["m"], {"m": (rows, {}, {})}, {}, prof)
    assert out["m"]["n"][PART] == 2
