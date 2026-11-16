"""Tier cells (lead 2026-11-14, for G2 / rc2): IMPLIED declares each check's tier (careful, competent,
explicit, unrated, contested) and each brief's domain. Stored chain elements carry ``check_tier`` and
``domain`` (stamped at grade time; ``regrade --stamp check_tier`` labels old rows, no grade moves). A
calibration that names ``<cell>:careful`` / ``<cell>:base`` rows splits a tiered unit into one unit per
group (its checks of that group, weighted), in the parent prompt's cluster. A family without tiers and
an old row without labels stay whole (untiered, never dropped)."""
from __future__ import annotations

import collections
import copy
import json
from pathlib import Path

import pytest

from crucibleforge import chains, profiles, regrade, scale


def _case(tiers, dom):
    return {"grader_config": {"check_tiers": tiers}, "meta": {"domain": dom}}


def test_tag_tiers_single_and_packed():
    single = _case({"a": "careful", "b": "competent"}, "dj")
    els = [{"check": "a"}, {"check": "b"}, {"check": "z"}]
    chains.tag_tiers(els, single)
    assert els == [{"check": "a", "check_tier": "careful", "domain": "dj"},
                   {"check": "b", "check_tier": "competent", "domain": "dj"}, {"check": "z"}]
    packed = {"multi_items": [_case({"a": "careful"}, "dj"), _case({"a": "explicit"}, "pay")]}
    els = [{"check": "a", "k": 1}, {"check": "a", "k": 2}, {"check": "a"}]
    chains.tag_tiers(els, packed)
    assert [e.get("check_tier") for e in els] == ["careful", "explicit", None]
    assert [e.get("domain") for e in els] == ["dj", "pay", None]
    # a family that declares no tiers: untouched
    els = [{"check": "a"}]
    assert chains.tag_tiers(els, {"grader_config": {}}) == [{"check": "a"}]


def _blk(els, cell_level=1):
    return {"id": "FLAT-implied1x3_L1-001", "family": "implied", "level": cell_level, "kind": "multi",
            "items": [1, 2, 3], "rung": 1, "attempt": 1, "part": "work/bk_implied", "score": 0.5,
            "passed": False, "outcome": "wrong", "elements": els, "pass_at": 0.5}


def _el(i, tier, score, w=1.0, k=1):
    e = {"id": f"u-i{k}-c{i}", "check": f"c{i}", "score": score, "weight": w, "k": k,
         "outcome": "pass" if score else "wrong"}
    if tier:
        e["check_tier"] = tier
    return e


CAL = {"cells": {"implied:L1:x3:careful": {"b": 0.5}, "implied:L1:x3:base": {"b": -1.0}}}


def test_a_tiered_unit_splits_into_one_unit_per_group_in_the_prompts_cluster():
    els = [_el(1, "careful", 1.0), _el(2, "careful", 0.0), _el(3, "careful", 0.0, k=2),
           _el(4, "explicit", 1.0, w=0.25), _el(5, "competent", 0.0), _el(6, "unrated", 1.0),
           _el(7, "contested", 0.0, w=0.0)]
    us = scale.units([{"chain": _blk(els)}], cal=CAL)
    by = {u["tier"]: u for u in us}
    assert set(by) == {"careful", "base"} and len(us) == 2
    assert by["careful"]["cell"] == "implied:L1:x3:careful" and by["careful"]["x"] == pytest.approx(1 / 3)
    assert by["base"]["x"] == pytest.approx((0.25 + 1.0) / 2.25)      # weighted; contested in no group
    assert by["careful"]["cluster"] == by["base"]["cluster"] == "FLAT-implied1x3_L1-001-r1-a1"
    # the tier cell is scored on its own row (never the parent's or a trend)
    a = scale.ability(us, CAL)
    assert a["theta"] is not None


def test_no_tier_rows_in_the_table_keeps_the_unit_whole():
    els = [_el(1, "careful", 1.0), _el(2, "explicit", 0.0)]
    us = scale.units([{"chain": _blk(els)}], cal={"cells": {"implied:L1:x3": {"b": 0.0}}})
    assert len(us) == 1 and us[0]["cell"] == "implied:L1:x3" and "tier" not in us[0]


def test_an_old_row_without_labels_is_untiered_not_dropped():
    els = [_el(1, None, 1.0), _el(2, None, 0.0)]
    us = scale.units([{"chain": _blk(els)}], cal=CAL)
    assert len(us) == 1 and us[0]["cell"] == "implied:L1:x3" and us[0]["x"] == pytest.approx(0.5)


def test_a_family_without_tiers_is_unchanged():
    blk = {**_blk([_el(1, None, 1.0)]), "family": "pytrap", "kind": "chain"}
    us = scale.units([{"chain": blk}], cal={**CAL, "cells": {**CAL["cells"], "pytrap:L1": {"b": 0.0}}})
    assert [u["cell"] for u in us] == ["pytrap:L1"]


def test_a_tier_cell_has_no_fallback():
    cal = scale.normalise({"cells": {"implied:L1": {"b": 0.3}}, "families": {"implied": {"base": 0.0, "step": 0.5}}})
    assert scale.item(cal, "implied:L1:careful") is None
    assert scale.item(cal, "implied:L1:x3:careful") is None
    assert scale.item(cal, "implied:L1:x3") == (0.3, 1.0)                # the packed fallback is unchanged


def test_stamp_verdict_labels_a_real_implied_reply(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "main-v1-imp")
    prof = profiles.load_profile("bench45")
    c = next(x for x in chains.chain_cases(prof) if x["id"].startswith("FLAT-implied1x3h_L3"))
    case = chains.materialize(chains.instance(c, 1, 1))
    row = {"chain": dict(case["chain"]), "finish_reason": "stop", "response_text": "", "grade": "ok"}
    chains.stamp_verdict([row], case)
    els = row["chain"]["elements"]
    want = {k + 1: (s["grader_config"]["check_tiers"], s["meta"]["domain"]) for k, s in enumerate(case["multi_items"])}
    assert els and all(e.get("check_tier") == want[e["k"]][0].get(e["check"]) for e in els if e["check"] != "?")
    assert all(e.get("domain") == want[e["k"]][1] for e in els if e.get("check_tier"))


# --- stored rows: a frozen copy (tests/fixtures/tier_cells_frozen_20261115.json) -------------------------
# The implied rows of D's results-ref-mainv1 / results-m1imp-dsflash (d-work/real2, as of 2026-11-15; reasoning,
# conversation, metrics and regrade history dropped, replies trimmed, prompts stored once), G2's hand-joined
# rj-acce-*.tsv rows for those two models and T's tiers-84c1760.json. Before 2026-11-15 the test read the live
# scratch folders and regraded them under the current tree: T's IMPLIED scrub pass-1 + halves-up (060085f) moved
# some checks, so the current grade no longer matched G2's join made under 84c1760. The units are compared on
# their STORED verdict (the one G2 joined), tier-labelled by ``regrade --stamp check_tier`` (no grade moves).

FROZEN = Path(__file__).with_name("fixtures") / "tier_cells_frozen_20261115.json"
_FZ: dict = {}


def _frozen():
    if not _FZ:
        _FZ.update(json.loads(FROZEN.read_text(encoding="utf-8")))
    return _FZ


def _stored(d):
    fz = _frozen()
    src = fz["dirs"][d]
    rows = copy.deepcopy(src["rows"])
    for r in rows:
        if isinstance(r.get("prompt"), dict) and "$ref" in r["prompt"]:
            r["prompt"] = fz["prompts"][r["prompt"]["$ref"]]
    return src["label"], src["composition"], rows


def _g2(label):
    fz = _frozen()
    tiers = fz["tiers"]["tiers"]
    out = collections.defaultdict(lambda: [0.0, 0.0])
    for r in fz["g2_rows"]:
        if r["model"] != label or r["prompt_match"] != "True":
            continue
        c, td = r["check"], tiers.get(r["domain"], {})
        t = "explicit" if c in ("x1", "x2", "x3") else td.get(f"{c}@L{r['level']}", td.get(c, "unrated"))
        w = float(r["weight"] or 0)
        if t == "contested" or w <= 0:
            continue
        a = out[(r["unit"], "careful" if t == "careful" else "base")]
        a[0] += w * (r["rejudge"] == "True")
        a[1] += w
    return out


@pytest.mark.parametrize("d, rule", [("results-ref-mainv1", "exclude"), ("results-m1imp-dsflash", None)])
def test_tier_cells_reproduce_g2s_hand_joined_units(monkeypatch, d, rule):
    label, comp, rows = _stored(d)
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    specs = {c["id"]: c for c in chains.chain_cases(profiles.load_profile("bench45"))}
    new = []
    for rec, grp in zip(regrade.plan_stamp(rows, specs, "check_tier", "t"), regrade.attempts(rows)):
        new += rec["rows"] if rec["status"] == "regrade" else grp
    cells = {scale.cell_of(r["chain"]) for r in new}
    cal = {"cells": {f"{c}:{g}": {"b": 0.0} for c in cells for g in ("careful", "base")}}
    us = scale.units(new, cal=cal, ran_out={c: rule for c in cells} if rule else None)
    g2 = _g2(label)
    got = [(u["x"], g2[k]) for u in us if u.get("tier") and (k := (u["id"].rsplit("-r", 1)[0], u["tier"])) in g2]
    assert len(got) >= 24                     # G2 joined one run: 12 prompts x careful/base
    assert all(abs(x - a / w) < 1e-9 for x, (a, w) in got if w)


def test_stamp_check_tier_labels_stored_rows_and_moves_no_grade(monkeypatch):
    label, comp, rows = _stored("results-ref-mainv1")
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    specs = {c["id"]: c for c in chains.chain_cases(profiles.load_profile("bench45"))}
    recs = regrade.plan_stamp(rows, specs, "check_tier", "t")
    assert {r["status"] for r in recs} == {"regrade"}
    for r, grp in zip(recs, regrade.attempts(rows)):
        old = grp[-1]
        new = r["rows"][-1]["chain"]
        assert new["score"] == old["chain"]["score"] == r["score_after"] == r["score_before"]
        assert [{k: v for k, v in e.items() if k not in chains.TIER_KEYS} for e in new["elements"]] == old["chain"]["elements"]
        assert r["rows"][-1].get("chain_revision") == old.get("chain_revision")
        assert any(e.get("check_tier") for e in new["elements"])
    # stamped rows are current; a stored label that disagrees is refused
    stamped = [x for r in recs for x in r["rows"]]
    assert {r["status"] for r in regrade.plan_stamp(stamped, specs, "check_tier", "t")} == {"current"}
    bad = copy.deepcopy(stamped)
    e = next(e for x in bad if x["chain"].get("elements") for e in x["chain"]["elements"] if e.get("check_tier"))
    e["check_tier"] = "nonsense"
    assert "refused" in {r["status"] for r in regrade.plan_stamp(bad, specs, "check_tier", "t")}


def test_a_cell_with_a_row_for_every_tier_group_counts_as_calibrated():
    from crucibleforge import routing
    cal = scale.normalise({"cells": {"implied:L1:x3:careful": {"b": 0.5}, "implied:L1:x3:base": {"b": -1.0},
                                     "implied:L3:x3:careful": {"b": 0.5}}})
    assert routing.calibrated(cal, "implied:L1:x3")
    assert not routing.calibrated(cal, "implied:L3:x3")          # base row missing
