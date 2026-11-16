"""IMPLIED grading-only widening (F-055 panel part 2): prompts and scenarios byte-identical to bb2cbd5, a ValueError passes the
panel-accepted checks, and every panel tier is recorded."""
import hashlib
import json

import pytest

from crucibleforge.gen import implied as im

#: digests of every prompt / every hidden scenario (ids, kinds, inputs; weights excluded: the contested checks weigh 0) (seeds 1-60 x every domain at L1 and L3, seeds 1-16 x every L5 brief) at bb2cbd5
PROMPTS_BB2CBD5 = "e789b6b760c31b72"
#: re-pinned 2026-11-14 (scrub): the bb2cbd5 scenarios minus L5 meds3 `critcap` and car3 `onecar` / `onecarex` (dropped, not
#: scored); recomputed on the base tree with exactly those three filtered out = this digest. Prompts unchanged.
#: re-pinned 2026-11-15 (scrub pass-1 coverage): the same scenarios plus the level-1 checks appended after the drawn ones
#: (dj blendb, invoices dueday, rsvp noguests, ledger oldfirst/rerev, expenses pending); with the builders' EXTRA
#: lists removed the digest is fd2e327f8f3004d5 again, so no existing scenario moved. Prompts unchanged.
#: re-pinned 2026-11-15 (FIXNOW 21+22): L5 meds3 `renag` scenario moved and `dup` dropped. With meds3 dup and renag filtered
#: out, the tag 6d563e6 and this tree give the same digest (c5259da08024e510), so no other scenario moved. Prompts unchanged.
SCENS_BB2CBD5 = "4e7ec19c04dbbd8a"


def _digests():
    h, hs = hashlib.sha256(), hashlib.sha256()
    for tier in (1, 3):
        for dom in im.DOMS_ALL + sorted(set(im.DOMS) - set(im.DOMS_ALL)):
            for sd in range(1, 61):
                i = im.make_instance(sd, tier, dom)
                h.update(i["prompt"].encode())
                hs.update(json.dumps([[e["id"], e["kind"], e["scen"]] for e in i["hidden"]["exps"]], sort_keys=True).encode())
    for dom in im.DOMS5:
        for sd in range(1, 17):
            i = im.make_instance(sd, 5, dom)
            h.update(i["prompt"].encode())
            hs.update(json.dumps([[e["id"], e["kind"], e.get("scen")] for e in i["hidden"]["exps"]],
                                 sort_keys=True, default=str).encode())
    return h.hexdigest()[:16], hs.hexdigest()[:16]


def test_prompts_and_scenarios_unchanged_since_bb2cbd5():
    assert _digests() == (PROMPTS_BB2CBD5, SCENS_BB2CBD5)


def _inst_with(dom, cid, tier):
    for sd in range(1, 400):
        i = im.make_instance(sd, tier, dom)
        if any(e["id"] == cid for e in i["hidden"]["exps"]):
            return sd, i
    raise AssertionError((dom, cid))


@pytest.mark.parametrize("dom,cid", sorted(im.ERR_ACCEPT_PANEL))
def test_valueerror_is_accepted_and_typeerror_is_not(dom, cid):
    tier = 1
    sd, inst = _inst_with(dom, cid, tier)
    names = list(inst["public"]["fn"].values())
    raise_all = "\n".join(f"def {n}(*a, **k):\n    raise ValueError('refused')\n" for n in names)
    type_all = raise_all.replace("ValueError", "TypeError")
    cfg = {"seed": sd, "tier": tier, "domain": dom, "variant": inst["public"]["params"].get("v"), "cpu_s": 20}
    ok = {r["id"]: r["pass"] for r in im.grade_implied("```python\n" + raise_all + "```", cfg)["results"]}
    bad = {r["id"]: r["pass"] for r in im.grade_implied("```python\n" + type_all + "```", cfg)["results"]}
    assert ok[cid] and not bad[cid]


def test_every_panel_accepted_check_has_a_tier():
    for dom, cid in im.ERR_ACCEPT_PANEL | im.ERR_JUDGED:
        assert im.CHECK_TIER.get((dom, cid)) in ("careful", "competent")
    assert set(im.CHECK_TIER.values()) <= {"careful", "competent", "explicit", "contested"}


def test_contested_checks_weigh_zero_and_compensation_holds_the_floor():
    for tier in (1, 3):
        for sd in range(1, 25):
            inst = im.make_instance(sd, tier, "backup")
            w = {e["id"]: e["w"] for e in inst["hidden"]["exps"]}
            assert w.get("nospace", 0) == 0 and w.get("pinnedspace", 0) == 0
            assert w["lastgood"] >= 1 and w["onlycopy"] >= 1
            cfg = {"seed": sd, "tier": tier, "domain": "backup", "variant": inst["public"]["params"].get("v"), "cpu_s": 20}
            pad = im.grade_implied("```python\n" + im.pad(inst) + "\n```", cfg)["score"]
            assert pad < 0.6
    assert all(im.CHECK_TIER.get(k[:2]) == "contested" for k in im.CONTESTED_W0)


def test_level_tier_overrides_from_the_panel():
    assert im.check_tier("monitor", "storm", "a", 1) == "contested"
    assert im.check_tier("monitor", "storm", "a", 3) == "careful"
    for cid in ("batstorm", "episode", "prompt"):
        assert im.check_tier("monitor", cid, "a", 1) == "careful"
        assert im.check_tier("monitor", cid, "a", 3) == "competent"
    assert im.check_tier("monitor", "storm", "x", 3) == "explicit"
    assert set(im.CHECK_TIER.values()) <= {"careful", "competent", "explicit", "contested"}
