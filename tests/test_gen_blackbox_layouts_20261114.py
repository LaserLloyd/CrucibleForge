"""BLACK BOX: the flags byte is graded against every bit layout the prompt allows (scrub review, 2026-11-14).

The prompt shows the decoded values of the first few intact records and the visible dump. When a shown record has
gps_lock == armed, or its flags byte is 0x00 / symmetric / a run of set bits, a second layout (gps bit, armed bit, three
contiguous link_q bits, all disjoint) reproduces everything shown, and the hidden key followed an unstated generator
convention. A decoder built on any layout the prompt allows must score as the true one; a layout the prompt rules out must not."""
import copy
import itertools
import json

import pytest

from crucibleforge.gen import blackbox as bb
from crucibleforge.gen import _blackbox_ff as F

TIER_FRAMES = {2: (18, 2, 4), 3: (24, 4, 2)}


def _flag_kind(S):
    return [t for t in S["types"] if any(f["enc"] == "bits" for f in t["fields"])][0]


def _layouts_from_prompt(seed, tier):
    """Independent of the grader: the layouts that reproduce every shown record and explain every visible flag byte."""
    inst = F.gen(seed, tier)
    S = inst["hidden"]["S"]
    t = _flag_kind(S)
    sh = {m["name"]: m["shift"] for f in t["fields"] if f["enc"] == "bits" for m in f["members"]}
    nf, nc, k = TIER_FRAMES[tier]
    _, vis, _ = F.make_dump(S, f"blackbox:pub:{seed}:{tier}", nf, nc, k, None)
    byte = lambda r: (int(r["gps_lock"]) << sh["gps_lock"]) | (int(r["armed"]) << sh["armed"]) | (r["link_q"] << sh["link_q"])
    shown = [r for r in inst["public"]["kdec"] if r.get("kind", t["name"]) == t["name"]]
    vb = {byte(r["rec"]) for r in vis if r["rec"].get("kind", t["name"]) == t["name"]}
    out = []
    for g, a, l in itertools.product(range(8), range(8), range(6)):
        if len({g, a, l, l + 1, l + 2}) < 5:
            continue
        mask = (1 << g) | (1 << a) | (7 << l)
        dec = lambda b: (bool(b >> g & 1), bool(b >> a & 1), b >> l & 7)
        if all(dec(byte(r)) == (r["gps_lock"], r["armed"], r["link_q"]) for r in shown) and all(b & ~mask == 0 for b in vb):
            out.append((g, a, l))
    return out, (sh["gps_lock"], sh["armed"], sh["link_q"])


def _decoder(inst, lay):
    S = copy.deepcopy(inst["hidden"]["S"])
    for f in _flag_kind(S)["fields"]:
        if f["enc"] == "bits":
            for m in f["members"]:
                m["shift"] = dict(zip(("gps_lock", "armed", "link_q"), lay))[m["name"]]
    return "import json\nS = json.loads(%r)\n" % json.dumps(S) + F._REF


def _seeds(tier, ambiguous, n):
    out, s = [], 0
    while len(out) < n and s < 400:
        s += 1
        H, true = _layouts_from_prompt(s, tier)
        assert true in H
        if (len(H) > 1) == ambiguous:
            out.append((s, H, true))
    return out


@pytest.mark.parametrize("seed,H,true", _seeds(3, True, 3))
def test_any_layout_the_prompt_allows_scores_as_the_true_one(seed, H, true):
    inst = bb._instance(seed, 3)
    ref = bb.judge(inst, _decoder(inst, true))
    for lay in H:
        j = bb.judge(inst, _decoder(inst, lay))
        assert j["valid"] and abs(j["q"] - ref["q"]) < 1e-9, (lay, j["note"])
        assert all(r["pass"] for r in j["results"] if r["group"] == "bits"), lay


@pytest.mark.parametrize("seed,H,true", _seeds(3, False, 2))
def test_a_layout_the_prompt_rules_out_gets_no_credit(seed, H, true):
    inst = bb._instance(seed, 3)
    g, a, l = true
    wrong = (a, g, l)                     # swap the two flag bits: the shown record rules it out (unique item)
    assert wrong not in H
    j = bb.judge(inst, _decoder(inst, wrong))
    assert not all(r["pass"] for r in j["results"] if r["group"] == "bits")


def test_layouts_attached_to_the_instance_match_the_prompt():
    for seed, tier in [(s, t) for t in (2, 3) for s in range(1, 13)]:
        H, true = _layouts_from_prompt(seed, tier)
        got = [L for L, _ in bb._instance(seed, tier)["hidden"]["flag_exps"]]
        assert sorted(got) == sorted(H) and got[0] == true


@pytest.mark.parametrize("seed,tier", [(1, 1), (2, 2), (3, 3), (5, 3), (4, 4)])
def test_an_empty_decoder_earns_nothing_on_either_scale(seed, tier):
    # scrub 2026-11-14: the points scale paid ~0.24 for `return []` (floor 10, anchor-relative) and 0 for a crash
    inst = bb._instance(seed, tier)
    cfg = {"seed": seed, "tier": tier}
    for body in ("def decode(data):\n    return []\n", "def decode(data):\n    return [{} for _ in range(3)]\n"):
        g = bb.grade_blackbox("```python\n" + body + "```", cfg)
        if g["q"] <= 0:
            assert g["score"] == 0.0 and g["points"] == 0.0, g["detail"]
    g = bb.grade_blackbox("```python\ndef decode(data):\n    return []\n```", cfg)
    assert g["q"] == 0.0 and g["score"] == 0.0
    assert sum(r["weight"] * r["credit"] for r in g["results"]) == 0.0      # the board's scale (check share)
