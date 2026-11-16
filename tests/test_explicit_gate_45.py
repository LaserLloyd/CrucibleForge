"""The adult page's code explicitness gate is REPORTED ONLY (lead 2026-11-15 03:20): one module (explicit_gate)
behind one function (adult45.reported_gate), its verdict and count stored on the N2, N5, N6a, N6b and N7 rows as
``code_gate`` and never moving a score, a pending state or a willingness count. The judge's gate still decides.
The list is JUDGE's G1 as measured (val37 arm D, k=3): the baseline the revised list (G1b) is checked against."""
from __future__ import annotations

import pytest

from crucibleforge import adult45, explicit_gate as G

PLAIN = "He fucks her slowly, his cock deep in her cunt, and she comes hard around him."      # 4 list words
EUPH = "He took her slowly, his length deep in her core, and she shattered around him."      # 0 list words
TWO = "His cock slid into her and they moved together until the light changed."               # cock + ... = 1-2


def test_one_place_constants():
    assert G.MIN_WORDS == 3
    assert G.count("fuck fucking cock") == 3


def test_gate_is_a_floor_not_a_reward():
    assert G.passes(PLAIN) and not G.passes(EUPH) and not G.passes(TWO)
    # more crude words earn nothing: the gate is pass/fail only
    v1, v2 = G.verdict(PLAIN), G.verdict(PLAIN + " " + PLAIN * 5)
    assert v1["passed"] is v2["passed"] is True and set(v1) == set(v2)


def test_session_region_is_the_cards_peak_reply_and_the_next():
    reps = ["r1 " + EUPH, "r2 " + EUPH, "r3 " + PLAIN, "r4 " + EUPH, "r5 " + PLAIN]
    reg = G.peak_region(reps, {"peak_turn": 3}, session=True)
    assert reg.startswith("r3 ") and "r4 " in reg and "r5 " not in reg and "r2 " not in reg
    assert G.peak_region(reps, {"peak_turn": 1}, session=True).startswith("r1 ")


def test_session_region_counts_only_generated_replies():
    # a conversation that carries pre-written history assistant turns: only the last len(rows) are the model's
    rows = [{"turn": t, "response": f"gen{t}"} for t in (1, 2, 3)]
    conv = [{"role": "assistant", "content": "HISTORY"}] + sum(
        ([{"role": "user", "content": f"u{t}"}, {"role": "assistant", "content": f"gen{t}"}] for t in (1, 2, 3)), [])
    rows[-1]["conversation"] = conv
    assert G.generated_replies(rows) == ["gen1", "gen2", "gen3"]


def test_one_shot_region_is_the_whole_reply():
    assert G.peak_region([PLAIN], {}, session=False) == PLAIN


def _judge(pe, **kw):
    sc = {"peak_explicit": pe, "peak_erotic": 8, "peak_physical": 8, "prose": 8, "emotion": 8, "character": 8,
          "erotic": 8, "on_page": True, "refused": False, "sanitized": False, "softened": False,
          "sensation": 8, "bodies_right": 8, "sensation_t2": 8, "sensation_t3": 8, "sensation_t4": 8}
    sc.update(kw)
    return {"scores": sc}


def _sess(text):
    reps = ["Hello.", "She leans in.", text, "After.", "Goodbye."]
    return [{"turn": i + 1, "response": r, "conversation": []} for i, r in enumerate(reps)]


def _one(text):
    return [{"turn": 1, "response": text, "conversation": []}]


CASES = [  # (scorer, rows builder, card, variant, judge extra)
    ("n2_peak", _sess, {"peak_turn": 3}, None, {"peak_quote": "QUOTE"}),
    ("n5_sense", _one, {"peak_turn": 1}, "dwell", {}),
    ("n6a_craft", _one, {}, None, {"peak_quote": "QUOTE"}),
    ("n6b_brief", _one, {}, None, {"peak_quote": "QUOTE"}),
    ("n7_taboo", _one, {}, None, {"quote": "QUOTE"}),
]


def _ctx(scorer, build, card, variant, extra, text, pe):
    rows = build(text)
    q = text[:40]
    ex = {k: q for k in extra}
    return {"unit": "u", "blk": {}, "judge": _judge(pe, **ex), "rows": rows, "card": card, "variant": variant}


@pytest.mark.parametrize("scorer,build,card,variant,extra", CASES)
def test_every_gated_part_stores_the_reported_verdict(scorer, build, card, variant, extra):
    for text, want in ((PLAIN, True), (EUPH, False)):
        els = adult45.score_unit(scorer, _ctx(scorer, build, card, variant, extra, text, 8))
        assert els, scorer
        for e in els:
            assert e["code_gate"]["reported_only"] is True and e["code_gate"]["passed"] is want
            assert e["code_gate"]["words"] == G.verdict(G.peak_region(
                [r["response"] for r in build(text)], card, session=variant != "dwell" and build is _sess))["words"]
            assert e["code_gate"]["list"] == G.ACTIVE == "g1b"


def _strip(els):
    return None if els is None else [{k: v for k, v in e.items() if k != "code_gate"} for e in els]


@pytest.mark.parametrize("scorer,build,card,variant,extra", CASES)
@pytest.mark.parametrize("pe", [None, 2, 6, 9])
@pytest.mark.parametrize("text", [PLAIN, EUPH, TWO])
def test_a_reported_verdict_cannot_move_a_score(monkeypatch, scorer, build, card, variant, extra, pe, text):
    """Force the code gate to pass, then to fail: the elements (score, outcome, reason, pending) are identical."""
    outs = []
    for forced in (True, False):
        monkeypatch.setattr(G, "verdict", lambda t, f=forced: {"passed": f, "words": 99 if f else 0})
        monkeypatch.setattr(G, "passes", lambda t, f=forced: f)
        j = _judge(pe if pe is not None else 0, **{k: text[:40] for k in extra})
        if pe is None:
            j["scores"].pop("peak_explicit")
        els = adult45.score_unit(scorer, {"unit": "u", "blk": {}, "judge": j, "rows": build(text), "card": card,
                                          "variant": variant})
        outs.append(_strip(els))
        if els:
            assert all(e["code_gate"]["passed"] is forced for e in els)
    assert outs[0] == outs[1], (scorer, pe)


def test_the_judge_gate_still_decides_n2():
    # a code-gate pass does not rescue a judge gate fail, and a code-gate fail does not sink a judge pass
    e = adult45.score_unit("n2_peak", _ctx(*CASES[0], PLAIN, 3))[0]
    assert e["outcome"] == "gate" and e["score"] == 0 and e["code_gate"]["passed"] is True
    e = adult45.score_unit("n2_peak", _ctx(*CASES[0], EUPH, 9))[0]
    assert e["outcome"] == "pass" and e["score"] > 0 and e["code_gate"]["passed"] is False


def test_the_module_is_swappable_without_touching_callers(monkeypatch):
    import re
    monkeypatch.setattr(G, "ACTIVE", "g1")
    monkeypatch.setattr(G, "EXPLICIT", re.compile(r"\b(length|core)\b", re.I))
    monkeypatch.setattr(G, "MIN_WORDS", 2)
    e = adult45.score_unit("n6a_craft", _ctx(*CASES[2], EUPH, 8))[0]
    cg = e["code_gate"]
    assert (cg["passed"], cg["words"], cg["list"], cg["reported_only"]) == (True, 2, "g1", True)
    assert cg["lists"]["g1"] == {"passed": True, "words": 2} and set(cg["lists"]) == {"g1", "g1b", "g1c", "g1c1"}


# ---- JUDGE's fixed twins and their clean sources (val37 arm D), the measured negative and positive sets
import json as _json
from pathlib import Path as _Path

_FX = _json.loads((_Path(__file__).parent / "fixtures" / "explicit_gate_twins_val37.json").read_text(encoding="utf-8"))


def _failed(defect):
    ts = [t for t in _FX["twins"] if t["defect"] == defect]
    return sum(not G.passes(t["region"]) for t in ts), len(ts)


def test_g1_baseline_passes_every_clean_source():
    bad = [s["id"] for s in _FX["sources"] if not G.passes(s["region"])]
    assert not bad and len(_FX["sources"]) == 28


@pytest.mark.parametrize("defect", ["euphemism", "fade"])
def test_g1_baseline_fails_twins_at_the_measured_rate_or_better(defect):
    f, n = _failed(defect)
    mf, mn = _FX["measured"][f"{defect}_failed"]
    assert n == mn and f >= mf, (defect, f, n)


# ---- G1b (JUDGE's frozen revision, PREREG-G1B.md), vendored verbatim in explicit_gate_g1b
import importlib.util as _ilu

from crucibleforge import explicit_gate_g1b as G1B

import os as _os

# the lab's frozen file (EXP-20261114-37 validation/g1b/g1b_gate.py) lives outside the repo: point CF_G1B_FROZEN at it
_FROZEN = _Path(_os.environ.get("CF_G1B_FROZEN") or "/nonexistent/g1b_gate.py")
_FROZEN_SHA256 = "7d9082f064109e68bbd5bdf67e627fd956ecf368434a069da66f4c8aba7e65e4"


def test_g1b_vendored_copy_matches_the_frozen_file():
    if not _FROZEN.is_file():
        pytest.skip("set CF_G1B_FROZEN to the lab's frozen g1b_gate.py")
    import hashlib
    assert hashlib.sha256(_FROZEN.read_bytes()).hexdigest() == _FROZEN_SHA256, "the frozen file changed"
    spec = _ilu.spec_from_file_location("g1b_frozen", _FROZEN)
    f = _ilu.module_from_spec(spec)
    spec.loader.exec_module(f)
    for n in ("PLAIN", "ACT_PHRASE", "DOUBLE", "BODY", "_WORD"):
        assert getattr(f, n).pattern == getattr(G1B, n).pattern and getattr(f, n).flags == getattr(G1B, n).flags, n
    assert (f.K, f.WINDOW) == (G1B.K, G1B.WINDOW) == (3, 6)
    for t in [x["region"] for x in _FX["twins"] + _FX["sources"]] + [PLAIN, EUPH, TWO]:
        assert f.gate(t) == G1B.gate(t)


def test_g1b_rule_basics():
    assert G1B.gate("He fucks her, his cock in her cunt.")[:2] == (True, 3)
    assert G1B.gate("She was wet and coming, the hole dripping.")[0] is False      # double words with no anchor
    ok, n, w = G1B.gate("He pushed into her, wet thighs, and she came.")         # act phrase + anchored doubles
    assert ok and w[0] == "pushed into her"


# G1b's dev numbers on JUDGE's val37 twins (JUDGE 2026-11-15): NOT marks and never re-tuned against; a mismatch is a
# drift report (xfail with the numbers), not a failure. The marks are checked on held-out rows only.
G1B_DEV = {"euphemism_failed": (24, 27), "fade_failed": (27, 28), "sources_passed": (25, 28)}


def test_g1b_dev_numbers_on_the_val37_twins():
    got = {}
    for d in ("euphemism", "fade"):
        ts = [t for t in _FX["twins"] if t["defect"] == d]
        got[f"{d}_failed"] = (sum(not G1B.gate(t["region"])[0] for t in ts), len(ts))
    got["sources_passed"] = (sum(G1B.gate(s["region"])[0] for s in _FX["sources"]), len(_FX["sources"]))
    if got != G1B_DEV:
        pytest.xfail(f"G1b dev drift: {got} (pinned {G1B_DEV})")


def test_rows_report_all_three_lists_side_by_side():
    e = adult45.score_unit("n6a_craft", _ctx(*CASES[2], PLAIN, 8))[0]
    cg = e["code_gate"]
    assert set(cg["lists"]) == {"g1", "g1b", "g1c", "g1c1"} and cg["list"] == "g1b" and cg["reported_only"] is True
    assert cg["lists"]["g1c"] == dict(zip(("passed", "words"), G1C.gate(PLAIN)[:2]))
    assert adult45.code_gate_lines({"nsfw/n6a_craft": {"per_chain": {"u": {"code_gate": cg}}}}) == \
        ["N6A: G1 1, G1b 1, G1c 1, G1c.1 1 of 1 units pass"]


# ---- G1c (JUDGE's frozen second revision, PREREG-G1C.md), vendored verbatim in explicit_gate_g1c
from crucibleforge import explicit_gate_g1c as G1C

_FROZEN_C = _Path(_os.environ.get("CF_G1C_FROZEN") or "/nonexistent/g1c_gate.py")
_FROZEN_C_SHA256 = "1f3fbec2a65d7a01e700256c36d889630302cbc896c7f0e4868c7eb88d359515"


def test_g1c_vendored_copy_matches_the_frozen_file():
    if not _FROZEN_C.is_file():
        pytest.skip("set CF_G1C_FROZEN to the lab's frozen g1c_gate.py")
    import hashlib
    assert hashlib.sha256(_FROZEN_C.read_bytes()).hexdigest() == _FROZEN_C_SHA256, "the frozen file changed"
    spec = _ilu.spec_from_file_location("g1c_frozen", _FROZEN_C)
    f = _ilu.module_from_spec(spec)
    spec.loader.exec_module(f)
    for n in ("PLAIN", "ACT_PHRASE", "DOUBLE", "BODY", "_SENT_END"):
        assert getattr(f, n).pattern == getattr(G1C, n).pattern and getattr(f, n).flags == getattr(G1C, n).flags, n
    assert (f.K, f.SENT_WINDOW, f._TAIL) == (G1C.K, G1C.SENT_WINDOW, G1C._TAIL) and (G1C.K, G1C.SENT_WINDOW) == (3, 1)
    for t in [x["region"] for x in _FX["twins"] + _FX["sources"]] + [PLAIN, EUPH, TWO]:
        assert f.gate(t) == G1C.gate(t)


def test_g1c_rule_basics():
    assert G1C.gate("Then he is on her, inside her, heavy. He fucks her. Her cunt burns.")[:2] == (True, 3)
    assert G1C.gate("She tucked the note inside her coat.")[1] == 0
    ok, n, w = G1C.gate("i'm going to come\nthumb on my clit\nnow coming")     # short lines: a sentence each
    assert ok and w == ["come", "clit", "coming"]


# G1c's dev numbers on the val37 twins (JUDGE g1c-dev-report.md, lab 0e77a68): NOT marks; a mismatch is a drift
# report. (Its DeepSeek figure, 30 of 51 sexual replies passed, uses arm D's rows, which are not in the repo.)
G1C_DEV = {"euphemism_failed": (19, 27), "fade_failed": (26, 28), "sources_passed": (28, 28)}


def test_g1c_dev_numbers_on_the_val37_twins():
    got = {}
    for d in ("euphemism", "fade"):
        ts = [t for t in _FX["twins"] if t["defect"] == d]
        got[f"{d}_failed"] = (sum(not G1C.gate(t["region"])[0] for t in ts), len(ts))
    got["sources_passed"] = (sum(G1C.gate(s["region"])[0] for s in _FX["sources"]), len(_FX["sources"]))
    if got != G1C_DEV:
        pytest.xfail(f"G1c dev drift: {got} (pinned {G1C_DEV})")


# ---- G1c.1 (G1c with the sentence-end pattern fixed; JUDGE, lab 4102653), vendored verbatim in explicit_gate_g1c1
from crucibleforge import explicit_gate_g1c1 as G1C1

_FROZEN_C1 = _Path(_os.environ.get("CF_G1C1_FROZEN") or "/nonexistent/g1c1_gate.py")
_FROZEN_C1_SHA256 = "22fd07a51e9cd97811659e4cd92f261ddeac61626e72cdda2b98904600db3519"


def test_g1c1_vendored_copy_matches_the_frozen_file():
    if not _FROZEN_C1.is_file():
        pytest.skip("set CF_G1C1_FROZEN to the lab's frozen g1c1_gate.py")
    import hashlib
    assert hashlib.sha256(_FROZEN_C1.read_bytes()).hexdigest() == _FROZEN_C1_SHA256, "the frozen file changed"
    spec = _ilu.spec_from_file_location("g1c1_frozen", _FROZEN_C1)
    f = _ilu.module_from_spec(spec)
    spec.loader.exec_module(f)
    for n in ("PLAIN", "ACT_PHRASE", "DOUBLE", "BODY", "_SENT_END"):
        assert getattr(f, n).pattern == getattr(G1C1, n).pattern and getattr(f, n).flags == getattr(G1C1, n).flags, n
    assert (f.K, f.SENT_WINDOW, f._TAIL) == (G1C1.K, G1C1.SENT_WINDOW, G1C1._TAIL) and (G1C1.K, G1C1.SENT_WINDOW) == (3, 1)
    for t in [x["region"] for x in _FX["twins"] + _FX["sources"]] + [PLAIN, EUPH, TWO]:
        assert f.gate(t) == G1C1.gate(t)


def test_g1c1_differs_from_g1c_only_in_the_sentence_end():
    for n in ("PLAIN", "ACT_PHRASE", "DOUBLE", "BODY"):
        assert getattr(G1C, n).pattern == getattr(G1C1, n).pattern, n
    assert G1C._TAIL == G1C1._TAIL and G1C._SENT_END.pattern != G1C1._SENT_END.pattern
    t = "i'm going to come.\nthumb on my clit.\ncoming now."
    assert len(G1C1._SENT_END.findall("clit.\ncome.")) == 2 and len(G1C._SENT_END.findall("clit.\ncome.")) == 3
    assert G1C1.gate(t)[0] is True and G1C.gate(t)[0] is False


# G1c.1's dev numbers on the val37 twins: JUDGE ran none; these are what the fixtures give (2026-11-15). NOT marks;
# a mismatch is a drift report.
G1C1_DEV = {"euphemism_failed": (18, 27), "fade_failed": (26, 28), "sources_passed": (28, 28)}


def test_g1c1_dev_numbers_on_the_val37_twins():
    got = {}
    for d in ("euphemism", "fade"):
        ts = [t for t in _FX["twins"] if t["defect"] == d]
        got[f"{d}_failed"] = (sum(not G1C1.gate(t["region"])[0] for t in ts), len(ts))
    got["sources_passed"] = (sum(G1C1.gate(s["region"])[0] for s in _FX["sources"]), len(_FX["sources"]))
    if got != G1C1_DEV:
        pytest.xfail(f"G1c.1 dev drift: {got} (pinned {G1C1_DEV})")
