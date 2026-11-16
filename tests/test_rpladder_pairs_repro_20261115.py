"""The engine's ladder path reproduces PAIRS's prototype from its STORED verdicts (no judge call): the prototype's
sessions (its own row choice, the older pilot run for 10 of 12 cases) and rungs (pairs-work/rungs.json), the
prototype wording (with its adult sentence: it is what those verdicts were made under). Every verdict is found by
the engine's request key, so a byte of difference in the engine's prompt building leaves a cell unplaced and fails.

Box-only: skipped where /var/tmp/mb45-build (the pilot folders and PAIRS's files) is absent. Nothing of those rows
is copied into the repository (Claude Code rows are validation-only)."""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from crucibleforge import rpladder as RL

PAIRS = Path("/var/tmp/mb45-build/pairs-work")
D2 = Path("/var/tmp/mb45-build/d2-work")
MODELS = ["sonnet", "haiku", "dsflash", "ref", "mm", "grok"]
#: PAIRS's figures (ladder-report-3rung.md / -2win-3rung.md, three-rung reading)
WANT3 = {"sonnet": 2.96, "dsflash": 2.52, "haiku": 2.10, "ref": 1.55, "mm": 1.47, "grok": 0.49}
WANT2 = {"sonnet": 2.88, "dsflash": 2.51, "haiku": 2.05, "ref": 1.54, "mm": 1.46, "grok": 0.49}

pytestmark = pytest.mark.skipif(not (PAIRS / "verdicts" / "ladder.jsonl").exists() or not D2.exists(),
                                reason="box-only: PAIRS's stored verdicts and the pilot folders")


def _sessions(cards):
    """PAIRS's choice: the first longest rp_long conversation per case id."""
    out, mid = {}, {}
    for m in MODELS:
        f = glob.glob(str(D2 / f"results-pilot-{m}" / "transcripts_*.jsonl"))[0]
        best = {}
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            if r.get("rubric") == "rp_long":
                if r["case_id"] not in best or len(r["conversation"]) > len(best[r["case_id"]]["conversation"]):
                    best[r["case_id"]] = r
        out[m] = {}
        for cid, r in sorted(best.items()):
            u = RL.unit_from_row(r, cards)
            seed = cid.split("_")[-1][:2]
            u.update(unit=f"{u['card']}:{seed}", seed=seed, model=m)
            out[m][u["unit"]] = u
    return out


@pytest.fixture(scope="module")
def world():
    return build_world()


def build_world():
    from crucibleforge.gen import rpsession as G
    bank = {c["id"]: c for c in G.load_bank()["cards"]}
    old = json.loads((PAIRS / "rungs.json").read_text(encoding="utf-8"))
    cards = {}
    for cid, r in old.items():
        rungs = {str(k): {"replies": r[f"R{k}"]["replies"], "sha": r[f"R{k}"]["sha"],
                          "source": {"model_id": r[f"R{k}"]["model"]} if k < 4 else {}} for k in RL.RUNGS}
        lad = {"rungs": rungs}
        lad["ladder_sha"] = RL.ladder_sha_of(lad)
        assert lad["rungs"]["4"]["replies"] == list(bank[cid]["reference"])
        cards[cid] = {"system": bank[cid]["system"], "turns": list(bank[cid]["turns"]),
                      "candidates": {"standard": lad}, "order": ["standard"]}
    S = _sessions(cards)
    jm = "pairs-prototype"
    cache = RL.VerdictCache(None)
    axes_text = RL.axis_lines()
    lads = {cid: {**RL.flat(cards)[f"{cid}#standard"], "key": f"{cid}#standard"} for cid in cards}

    def put(lad, unit, w, ax, k, anchor, row):
        for req in RL.pair_requests(lad, unit, w, ax, k, anchor, "prototype", jm, axes_text):
            if req["subj_is"] == row["subj_is"]:
                cache.put(req["key"], {"better": row.get("better"), "reason": row.get("reason")})

    rej = {}
    for line in open(PAIRS / "verdicts" / "ladder.jsonl", encoding="utf-8"):
        r = json.loads(line)
        lad = lads[r["card"]]
        if r["id"].endswith("|rejudge"):
            rej[r["id"][:-len("|rejudge")]] = r
            continue
        k = r["rung"]
        if r["group"] == "rung":
            up = int(r["subject"][1:])
            unit = {"card": r["card"], "users": cards[r["card"]]["turns"],
                    "replies": lad["rungs"][str(up)]["replies"], "unit": r["unit"], "model": None}
            put(lad, unit, r["window"], r["axis"], k, lad["rungs"][str(k)]["replies"], r)
        else:
            u = S[r["subject"]][r["unit"]]
            rt = RL.rung_text(lad, k, u, _other(S, u))
            put(lad, u, r["window"], r["axis"], k, rt[0], r)
    for line in open(PAIRS / "verdicts" / "verdicts.jsonl", encoding="utf-8"):     # run 1 = the R4 comparisons
        r = json.loads(line)
        if r["group"] != "model" or r["id"].endswith("|rejudge"):
            continue
        u = S[r["subject"]][r["unit"]]
        lad = lads[u["card"]]
        put(lad, u, r["window"], r["axis"], 4, lad["rungs"]["4"]["replies"], r)
    chk = RL.rung_check(cards, None, cache, judge_model=jm, wording="prototype", ask=False)
    # the prototype's check had R2>R1 and R3>R2 (R4>R3 came from other context, it never gates): both present?
    for key, cell in chk["cells"].items():
        assert None not in cell["steps"]["R2>R1"] + cell["steps"]["R3>R2"], key
    chk["checked"] = list(RL.flat(cards))
    return {"cards": cards, "S": S, "cache": cache, "check": chk, "jm": jm, "rej": rej}


def _other(S, u):
    o = "s2" if u["seed"] == "s1" else "s1"
    return S[u["model"]].get(f"{u['card']}:{o}")


def _positions(world, windows):
    out = {}
    for m in MODELS:
        units = list(world["S"][m].values())
        got = RL.place_units(units, world["cards"], None, world["cache"], judge_model=world["jm"],
                             check=world["check"], windows_used=windows, wording="prototype",
                             own_other=lambda u: _other(world["S"], u), ask=False)
        ps = [p["position"] for p in got.values() if not p.get("no_ladder")]
        assert all(p["complete"] for p in got.values()), m
        out[m] = sum(ps) / len(ps)
    return out


def test_fifteen_of_twentyfour_card_axes_usable(world):
    usable = [k for k, c in world["check"]["cells"].items() if c["usable"]]
    assert len(usable) == 15
    assert not any(k.startswith("RPS2-greenhouse") for k in usable)


@pytest.mark.parametrize("windows,want", [((1, 2, 3), WANT3), ((1, 3), WANT2)])
def test_positions_reproduce_the_prototype(world, windows, want):
    got = _positions(world, windows)
    for m, v in want.items():
        assert abs(got[m] - v) <= 0.05, (m, round(got[m], 3), v)


def test_rejudge_agreement_is_the_prototypes(world):
    main = {}
    for line in open(PAIRS / "verdicts" / "ladder.jsonl", encoding="utf-8"):
        r = json.loads(line)
        main[r["id"]] = r
    same = sum(RL.side(r.get("better"), r["subj_is"]) == RL.side(main[k].get("better"), main[k]["subj_is"])
               for k, r in world["rej"].items() if k in main)
    assert (same, len(world["rej"])) == (525, 530)
