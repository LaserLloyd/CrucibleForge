"""pk_implied5 meds3 (FIXNOW 21 + 22, lead 2026-11-15; from the scrub, SCRUB3).

21 `renag`: the earlier reminder was stamped 07:00 local while the dose is at 07:00, 08:00 or 09:00, so it predated the
dose. The hidden scenario now stamps it at the dose time and "now" at dose + 3 h. Hidden scenario only; prompt unchanged.
22 `dup`: an unstated same-key dedup inside the gate whose only stated rule is 3 per 60 minutes; not scored (DROPPED),
kept in build() so every other scenario stays byte-identical. `nodup` measures dedup at the plan level.
The two ship together (22 alone would lift a program that fails the broken renag to full marks).
Fixture: the stored rows of items 02 and 05 for four models with their verdicts on the tag (before) and after."""
import copy
import json
from pathlib import Path

import pytest

from crucibleforge import gen
from crucibleforge.gen import _implied3 as M
from crucibleforge.gen import implied as im

ROOT = Path(__file__).resolve().parent.parent
FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "pk_implied5_meds3_rows_20261115.json")
                 .read_text(encoding="utf-8"))["rows"]


def _cases():
    s = json.loads((ROOT / "cases/ladder/work/c1_implied/set-1.json").read_text(encoding="utf-8"))
    out = {}
    for c in s["cases"]:
        if c["id"] in ("PK-C1-IMPLIED-L5-02", "PK-C1-IMPLIED-L5-05"):
            c = copy.deepcopy(c)
            gen.materialize_case(c)
            out[c["id"]] = c["grader_config"]
    return out


CASES = _cases()


def test_dup_is_dropped_and_prompt_unchanged():
    assert M.DROPPED == {"critcap", "dup"}
    for gc in CASES.values():
        inst = im._instance(gc["seed"], gc["tier"], gc["domain"], gc.get("variant"))
        ids = {e["id"] for e in inst["hidden"]["exps"]}
        assert "dup" not in ids and "renag" in ids and "nodup" in ids


def _meds_seeds(n=3):
    out, s = [], 0
    while len(out) < n and s < 200:
        s += 1
        if im.make_instance(s, 5)["public"]["domain"] == "meds3":
            out.append(s)
    return out


@pytest.mark.parametrize("seed", _meds_seeds())
def test_reference_passes_every_check(seed):
    inst = im.make_instance(seed, 5)
    assert all(r["pass"] for r in im._judge_all(inst, im.reference_code(inst))["results"])


@pytest.mark.parametrize("r", FIX, ids=lambda r: f"{r['item'][-2:]}-{r['model']}")
def test_stored_rows_after_the_fix(r):
    g = im.grade_implied(r["response"], CASES[r["item"]], r["finish_reason"])
    assert sorted(x["id"] for x in g["results"] if not x.get("pass")) == r["fails_after"]
    assert g["score"] == pytest.approx(r["score_after"])


def test_the_movers_are_the_scrubs():
    moved = {(r["item"][-2:], r["model"], r["score_before"], r["score_after"]) for r in FIX
             if r["score_before"] != r["score_after"]}
    assert moved == {("02", "mm", 0.65, 0.3), ("02", "sonnet", 0.65, 0.3), ("05", "dsflash", 0.65, 0.3)}
    # the only renag pass -> fail on a stored row is Sonnet's item 02 (lead 2026-11-15)
    flips = {(r["item"][-2:], r["model"]) for r in FIX
             if "renag" not in r["fails_before"] and "renag" in r["fails_after"]}
    assert flips == {("02", "sonnet")}
