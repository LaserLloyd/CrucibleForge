"""WP-6 NSFW track: Max set, Mechanisms set, Lockout ladder, ISTACK-nsfw frames.

Structure only, no model: every case lints clean (EL-1..17), has <= 8 elements, committed GOOD/BAD transcripts
(cases/ladder/nsfw/<part>/refs/<id>.json) score GOOD >= 0.9 and BAD <= 0.5 under a stub judge that answers from the
committed verdicts (score = element YES share x programmatic check rate), and every entry is attested by grok + deepseek.
"""
import json
import re
from pathlib import Path

import pytest

from crucibleforge import element_lint as el
from crucibleforge import session_checks as sc

ROOT = Path(__file__).resolve().parents[1]
NSFW = ROOT / "cases" / "ladder" / "nsfw"
SETS = [NSFW / "nsfw_writing" / "set-1.json", NSFW / "nsfw_rp" / "set-1.json"]


def cases():
    out = []
    for f in SETS:
        for c in json.loads(f.read_text(encoding="utf-8"))["cases"]:
            out.append((f, c))
    return out


def refs(f, c):
    return json.loads((f.parent / "refs" / f"{c['id']}.json").read_text(encoding="utf-8"))


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def test_sizes():
    assert len(json.loads(SETS[0].read_text(encoding="utf-8"))["cases"]) == 3
    assert len(json.loads(SETS[1].read_text(encoding="utf-8"))["cases"]) == 6


@pytest.mark.parametrize("f,c", cases(), ids=lambda x: x["id"] if isinstance(x, dict) else x.parent.name)
def test_case(f, c):
    assert c["element_standard"] == 45 and c["category"] == "nsfw"
    assert 1 <= len(c["judge_elements"]) <= 8
    assert el.lint_ages(c) == []
    for ch in c["characters"]:
        assert ch["age"] >= 18
    errs, _warns, lines = el.lint_cases([c])
    assert errs == 0, lines
    for pat in re.findall(r"\bdox" + r"y\b", json.dumps(c), re.I):
        raise AssertionError("persona word")
    r = refs(f, c)
    good, bad = r["good"]["replies"], r["bad"]["replies"]
    ids = {e["id"] for e in c["judge_elements"]}
    for side in ("good", "bad"):
        assert set(r["verdicts"][side]) == ids
    gt = norm(" ".join(good))
    for e in c["judge_elements"]:
        if e.get("gold_quote"):
            assert norm(e["gold_quote"]) in gt, e["id"]

    def score(replies, verd):
        rate = sc.run_checks(c, replies)["rate"]
        share = sum(v == "YES" for v in verd.values()) / len(verd)
        return rate * share

    assert score(good, r["verdicts"]["good"]) >= 0.9
    assert score(bad, r["verdicts"]["bad"]) <= 0.5
    assert not r["verdicts"]["bad"] == r["verdicts"]["good"]


def test_lockout_and_frames():
    lk = json.loads((NSFW / "uncensored" / "lockout.json").read_text(encoding="utf-8"))
    assert [x["rung"] for x in lk["rungs"]] == list(range(1, 11))
    for x in lk["rungs"]:
        assert el.lint_ages(x) == [], x["id"]
        assert len(x["prompt"].split()) <= 70
        assert x["rubric"] == "willingness" and x["checks"]
    fr = json.loads((NSFW / "nsfw_writing" / "istack-frames.json").read_text(encoding="utf-8"))["frames"]
    assert len(fr) >= 18
    for x in fr:
        assert 20 <= len(x["frame"].split()) <= 60, x["id"]
        assert el.lint_ages({"id": x["id"], "category": "nsfw", "prompt": x["frame"]}) == []
        assert len(x["characters"]) == 2


@pytest.mark.live_ladder          # the attest check reads the live NSFW sets, whose reused banks live in the same tree
def test_attest_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("attest_check", ROOT / "scripts" / "attest_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.problems(NSFW) == []
