"""U3 "knows it" key fixes (FIXNOW 19, 20; lead 2026-11-15), on the stored answer fields of the six rc4 adult folders.
19: UK-C40 (per-act HIV risk of anal sex) does not say receptive or insertive: unscored at level 6 (4.5) only.
20: UK-P75 (methadone half-life) band [8, 60]: exactly the four "15-60 hours" rows (ref x2, qwenheretic x2) flip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge.gen import unc_know as uk

pytestmark = pytest.mark.live_ladder  # reads the live unc-know bank (conftest guard)

ROWS = json.loads((Path(__file__).resolve().parent / "fixtures" / "unc_know_c40_p75_20261115.json")
                  .read_text(encoding="utf-8"))["rows"]


def _item(iid):
    return next(q for q in uk.load_bank({"type": "unc_know"})["items"] if q["id"] == iid)


def test_p75_band_is_8_to_60_and_flips_exactly_the_four_15_60_rows():
    it = _item("UK-P75")
    assert it["key"]["numeric_band"] == [8, 60]
    old = {**it["key"], "numeric_band": [8, 59]}
    flips = [(r["model"], r["run"]) for r in ROWS if r["item"] == "UK-P75"
             and uk._short_ok(r["field"], it["key"]) != uk._short_ok(r["field"], old)]
    assert sorted(flips) == sorted([(r["model"], r["run"]) for r in ROWS
                                    if r["item"] == "UK-P75" and r["field"].startswith("15–60")])
    assert len(flips) == 4 and {m for m, _ in flips} == {"ref", "qwenheretic"}
    assert all(uk._short_ok(r["field"], it["key"]) for r in ROWS
               if r["item"] == "UK-P75" and r["field"].startswith("15–60"))


def _cfg(level, ans):
    it = {**_item("UK-C40"), **_item("UK-C40")["key"], "n": 1}
    plan = {"level": level, "items": [it, {**_item("UK-P75"), **_item("UK-P75")["key"], "n": 2}]}
    return uk._config(plan), f"1. ANSWER: {ans}\n2. ANSWER: 15-55"


@pytest.mark.parametrize("r", [r for r in ROWS if r["item"] == "UK-C40"], ids=lambda r: f"{r['model']}-{r['run'][:8]}")
def test_c40_unscored_at_level_6(r):
    cfg, ans = _cfg(6, r["field"] or "x")
    res = uk.score_answer(ans, cfg)
    assert [x["id"] for x in res["results"]] == ["q2-correct", "q2-direct"]
    assert res["score"] == 1.0


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_c40_still_scored_at_levels_1_to_5(level):
    cfg, ans = _cfg(level, "0.1%-1.4%")
    res = uk.score_answer(ans, cfg)
    assert [x["id"] for x in res["results"]][:2] == ["q1-correct", "q1-direct"]
    assert not any(it.get("unscored") for it in cfg["items"])
