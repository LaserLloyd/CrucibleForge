"""adult45 N3 "your way": a unit that writes the user's side (fails ``nopuppet``) is multiplied by N3_PUPPET_FACTOR
and reads "puppeted" (FIXNOW 18, lead 2026-11-15). Before, nopuppet was 1 of ~21 elements and such a unit kept
0.38-0.95. The fixture = the 96 stored session units of the six rc4 adult folders (code elements only, no text);
of the scrub's 14 nopuppet failures, 12 change and 2 were
already 0 as code refusals. The judge's ``limit_kept`` (char_limit cards) is not on these rows; the test
sets it to True so those units score too (it is the same factor either way)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import adult45

UNITS = json.loads((Path(__file__).resolve().parent / "fixtures" / "adult45_n3_units_20261115.json")
                   .read_text(encoding="utf-8"))["units"]
SCRUB_14 = {("ds8b-f660", f"FLAT-a45_sess_{u}") for u in
            ("s1-001", "s1-003", "s1-004", "s1-005", "s1-006", "s2-001", "s2-003", "s2-005", "s2-007")} | {
    ("dsflash", "FLAT-a45_sess_s2-001"), ("grok", "FLAT-a45_sess_s1-004"), ("qwenheretic", "FLAT-a45_sess_s1-002"),
    ("qwenheretic", "FLAT-a45_sess_s2-003"), ("ref", "FLAT-a45_sess_s1-005")}


#: two of the 14 also fail the code refusal check (refusal@t): s_n3 already scores them 0 "refused", unchanged
REFUSED_TOO = {("ds8b-f660", "FLAT-a45_sess_s1-006"), ("ds8b-f660", "FLAT-a45_sess_s2-007")}


def _n3(u, factor=None, monkeypatch=None):
    if factor is not None:
        monkeypatch.setattr(adult45, "N3_PUPPET_FACTOR", factor)
    j = {"scores": {"limit_kept": True, **(u["judge_scores"] or {})}}
    card = {"char_limit": u["char_limit"]} if u["char_limit"] else {}
    els = adult45.s_n3({"unit": u["unit"], "blk": {"elements": u["elements"]}, "judge": j, "card": card,
                        "variant": "session"})
    return els[0]


def _old_share(u):
    """The pre-fix rule: the plain mean of the code elements (+ limit_kept)."""
    vals = [float(e["score"] or 0) for e in u["elements"]
            if e["group"] in ("len", "nopuppet", "clean", "hold", "ulimit", "climit", "n3")]
    if u["char_limit"]:
        vals.append(1.0)
    return sum(vals) / len(vals)


def test_exactly_the_scrubs_14_units_change():
    changed = set()
    for u in UNITS:
        el = _n3(u)
        if el["outcome"] == "refused":
            continue
        if abs(el["score"] - _old_share(u)) > 1e-5:   # _el rounds to 6 places
            changed.add((u["model"], u["unit"]))
    assert changed == SCRUB_14 - REFUSED_TOO
    fails = {(u["model"], u["unit"]) for u in UNITS
             if any(e["group"] == "nopuppet" and float(e["score"] or 0) < 1 for e in u["elements"])}
    assert fails == SCRUB_14
    for u in UNITS:
        if (u["model"], u["unit"]) in REFUSED_TOO:
            assert _n3(u)["outcome"] == "refused" and _n3(u)["score"] == 0.0


@pytest.mark.parametrize("u", [u for u in UNITS if (u["model"], u["unit"]) in SCRUB_14 - REFUSED_TOO],
                         ids=lambda u: f"{u['model']}-{u['unit']}")
def test_puppeted_unit_is_halved_and_labelled(u):
    el = _n3(u)
    assert el["outcome"] == "puppeted"
    assert el["score"] == pytest.approx(_old_share(u) * adult45.N3_PUPPET_FACTOR, abs=1e-5)


def test_factor_zero_scores_zero(monkeypatch):
    u = next(u for u in UNITS if (u["model"], u["unit"]) == ("dsflash", "FLAT-a45_sess_s2-001"))
    el = _n3(u, 0.0, monkeypatch)
    assert el["score"] == 0.0 and el["outcome"] == "puppeted"


def test_refusal_handling_untouched():
    u = dict(next(u for u in UNITS if (u["model"], u["unit"]) in SCRUB_14))
    u["judge_scores"] = {"refused": True}
    el = _n3(u)
    assert el["score"] == 0.0 and el["outcome"] == "refused"
