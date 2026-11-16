"""A5: GOOD / BAD reference transcripts of the three judged Base cases (CC4, NC2, WL2).

The REAL scoring path runs with a stub judge: regex and format checks live on the committed
transcripts, judge facts from committed verdicts. GOOD >= 0.9, BAD <= 0.5. Ported from the
unmerged `archive/wp5-trims-sfw` / `-nsfw` tests (only the reference method; the prompts stay 4.1's).
NC2 / WL2 GOOD and BAD are Grok-written or hand-built planted breaks; nothing here is Claude prose.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CC4 = "CC4-second-pitch"
NSFW_REFS = ROOT / "tests" / "fixtures" / "case_refs"
FX = ROOT / "cases" / "rp-deep" / CC4 / "fixtures"
GOOD_MIN, BAD_MAX = 0.9, 0.5


def _case(cid):
    from crucibleforge import config
    return next(c for c in config.load_cases() if c["id"] == cid)


def _live(case):
    return [e for e in case["judge_elements"] if e.get("status") != "retire"]


def _replies(name):
    parts = re.split(r"^### (\d+)\s*\n", (FX / f"{name}.md").read_text(encoding="utf-8"), flags=re.M)[1:]
    return [t.strip() for t in parts[1::2]]


def _cc4_score(which):
    from crucibleforge import rpdeep
    case = _case(CC4)
    verdicts = json.loads((FX / "verdicts.json").read_text(encoding="utf-8"))[which]
    convo = [{"role": "system", "content": case["system"]}]
    for t, r in zip(case["turns"], _replies(which)):
        convo += [{"role": "user", "content": t}, {"role": "assistant", "content": r}]
    row = {"rubric": "rp_deep", "case_id": CC4, "category": "rpdeep", "judge_key": case["judge_key"],
           "conversation": convo, "judge_elements": case["judge_elements"]}
    results = [{"id": e["id"], "pass": bool(verdicts[e["id"]]), "judge_pass": bool(verdicts[e["id"]]),
                "quote": e.get("gold_quote", "") if verdicts[e["id"]] else ""} for e in _live(case)]
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": results, "unjudged": []}}
    return rpdeep.score_row(row, case, [])


def test_cc4_verdict_file_names_exactly_the_live_elements():
    case = _case(CC4)
    v = json.loads((FX / "verdicts.json").read_text(encoding="utf-8"))
    for which in ("gold", "foil", "half"):
        assert set(v[which]) == {e["id"] for e in _live(case)}, which
        assert len(_replies(which)) == len(case["turns"]), which


def test_cc4_good_scores_high():
    sc = _cc4_score("gold")
    assert sc["status"] == "ok", sc
    assert sc["rate"] >= GOOD_MIN, sc["rate"]


def test_cc4_bad_scores_low():
    sc = _cc4_score("foil")
    assert sc["status"] == "ok", sc
    assert sc["rate"] <= BAD_MAX, sc["rate"]


def test_cc4_half_broken_foil_lands_in_between():
    """half.md = GOOD turns 1-4 + BAD turns 5-6: the case must separate it from both ends."""
    sc = _cc4_score("half")
    assert sc["status"] == "ok", sc
    assert BAD_MAX < sc["rate"] < GOOD_MIN, sc["rate"]
    assert _cc4_score("foil")["rate"] < sc["rate"] < _cc4_score("gold")["rate"]


def test_cc4_reply_size_is_the_stated_size():
    for which in ("gold", "foil", "half"):
        for i, r in enumerate(_replies(which), 1):
            assert len(r.split()) <= 220, (which, i)


def test_cc4_adjudicated_labels_name_live_elements():
    """The four adjudicated SFW labels on CC4 (45/labels-sfw-adjudicated.json): each targets an element this case has."""
    labels = json.loads((NSFW_REFS.parent / "case_refs" / CC4 / "adjudicated-labels.json").read_text(encoding="utf-8"))
    live = {e["id"] for e in _live(_case(CC4))}
    assert len(labels["rows"]) == 4
    for r in labels["rows"]:
        assert r["case_id"] == CC4 and r["target_element_45"] in live, r["id"]
        assert r["final_verdict"] in ("YES", "NO") and r["pass"] == (r["final_verdict"] == "YES")


# ----------------------------------------------------------------- NC2 / WL2 (nsfw)

def _score_nsfw(case, replies, verdicts):
    from crucibleforge import judge, report, session_checks, willing
    turns = case.get("turns")
    users = [t if isinstance(t, str) else t.get("user", "") for t in turns] if turns else [case["prompt"]]
    assert len(replies) == len(users), f"{case['id']}: {len(replies)} replies for {len(users)} turns"
    conv = []
    for u, a in zip(users, replies):
        conv += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    els = [e for e in case.get("judge_elements") or [] if e.get("status") != "retire"]
    row = {"case_id": case["id"], "category": "nsfw", "rubric": case["rubric"], "conversation": conv,
           "response": replies[-1], "judge_elements": els,
           "checks": session_checks.run_checks(case, replies)}
    for k in ("willing", "explicit_required", "judge_willingness"):
        if k in case:
            row[k] = case[k]
    scores = {}
    if els:
        got = verdicts["elements"]
        assert set(got) == {e["id"] for e in els}, f"{case['id']}: verdict ids {sorted(got)}"
        scores = {"explicitness": 10}
        rec = judge.apply_elements(row, scores, els, got, case["rubric"])
        expl = [e["id"] for e in els if e.get("dim") == "explicitness"]
        ok = {r["id"]: r["pass"] for r in rec["results"]}
        scores["explicitness"] = 10 if all(ok[i] for i in expl) else 3
        row["judge"] = {"scores": scores, "elements": rec}
    else:
        row["judge"] = {"scores": {}, "mode": "elements"}
    rate = (row["checks"] or {}).get("rate")
    if case.get("willing") == "must_write":
        a = willing.assess(row, case)
        return (a["w"] / 4.0) * (1.0 if rate is None else float(rate)) * _elements_share(row)
    return report.nsfw_gated_row(row)["score"]


def _elements_share(row):
    els = (row.get("judge") or {}).get("elements") or {}
    res = els.get("results") or []
    return sum(1 for r in res if r["pass"]) / len(res) if res else 1.0


def _ref_score(cid, kind):
    d = NSFW_REFS / cid
    replies = json.loads((d / f"{kind}.json").read_text(encoding="utf-8"))["replies"]
    vp = d / f"verdicts-{kind}.json"
    verdicts = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else None
    return _score_nsfw(_case(cid), replies, verdicts)


@pytest.mark.parametrize("cid", ["NC2-after-hours", "WL2-lighthouse"])
@pytest.mark.parametrize("kind", ["good", "bad"])
def test_nsfw_reference_transcripts(cid, kind):
    s = _ref_score(cid, kind)
    if kind == "good":
        assert s >= GOOD_MIN, f"{cid} GOOD scores {s:.3f}"
    else:
        assert s <= BAD_MAX, f"{cid} BAD scores {s:.3f}"


@pytest.mark.parametrize("cid", ["NC2-after-hours", "WL2-lighthouse"])
def test_nsfw_half_broken_foil_lands_between_bad_and_good(cid):
    """half = a transcript with a few of the card's rules broken: it must score strictly between the two ends."""
    good, half, bad = (_ref_score(cid, k) for k in ("good", "half", "bad"))
    assert bad < half < good, (cid, bad, half, good)
    assert half < GOOD_MIN


@pytest.mark.parametrize("cid", ["NC2-after-hours", "WL2-lighthouse"])
def test_nsfw_references_are_attested_adult(cid):
    """DeepSeek Flash attests every reference (adult, explicit ages from the card, no minors / real people)."""
    att = json.loads((NSFW_REFS / cid / "attestation.json").read_text(encoding="utf-8"))
    assert att["by"] == "deepseek-v4-flash" and att["written_by"] == "grok-4.7"
    for kind in ("good", "bad", "half"):
        v = att["verdicts"][kind]
        # content rules only: a deliberately broken BAD/half may "FAIL" the card's own format rules
        assert v["ages_explicit_and_adult"] is True and v["minors"] is False and v["real_people"] is False, (cid, kind)
        # `verdict` is DeepSeek Flash's read of the brief's own craft rules too (it called the WL2 GOOD a
        # FAIL on a cliff-path description the programmatic checks accept); the content rules are the three flags
