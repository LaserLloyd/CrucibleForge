"""Fix round 3.10, lane A: NSFW grading + check credit.

B1 heat skip (NMX1), NC1 explicit-t4-t6 min_hits, B5 fade / throat false
positives, I-sanitized engagement, I-length near-miss credit, check_credit as
the only reader of check results in report.py, I-judge-samples (NC1/NC2 at 3
samples), and the n_excluded contract for the "partial" marker (lane D).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from crucibleforge import config, report, session_checks
from crucibleforge.session_checks import (FADE_RE, check_credit, check_fade_to_black,
                                          check_forbid_regex, check_word_range, run_checks)

ROOT = Path(__file__).resolve().parent.parent


def _cases() -> dict:
    out = {}
    for p in sorted((ROOT / "cases").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for c in d if isinstance(d, list) else d.get("cases", []):
            out[c["id"]] = c
    return out


def _check(case_id: str, check_id: str) -> dict:
    return next(c for c in _cases()[case_id]["checks"] if c["id"] == check_id)


# ------------------------------------------------------------ B1 heat skip
def _gated_case_set() -> list[dict]:
    """Every case the board scores with nsfw_gated: the profile's gated parts
    plus every ladder pack question of an erotic rubric."""
    from crucibleforge import ladder
    prof = yaml.safe_load((ROOT / "profiles" / "bench.yaml").read_text(encoding="utf-8"))
    ids = set()
    for cat in (prof.get("scores") or {}).values():
        for part in (cat.get("parts") or {}).values():
            if "nsfw_gated" in (part.get("scoring") or []):
                ids.update(part.get("cases") or [])
    cases = _cases()
    out = [cases[i] for i in sorted(ids)]
    out += [c for c in ladder.inline_cases()
            if c.get("rubric") in ("nsfw_craft", "erp_session")]
    return out


def test_heat_skip_is_exactly_nmx1():
    """A later case that silently loses its explicitness elements (or its
    explicit_required tag) must fail here, not quietly drop its heat factor."""
    gated = _gated_case_set()
    assert len(gated) >= 6
    skipped = {c["id"] for c in gated if report.nsfw_heat_skipped(c)}
    # 4.0: NMX1 (the only heat-skipped case) left the standard board
    assert skipped == set()
    nmx1 = next(c for c in config.load_cases(["nsfw"]) if c["id"] == "NMX1-needle-and-gull")
    assert report.nsfw_heat_skipped(nmx1)


def _row(case_id, *, scores=None, elements=None, checks=None, **kw):
    r = {"case_id": case_id, "category": "nsfw", "rubric": "erp_session",
         "needs_judge": True, "judge": {"scores": scores or {}, "elements": {"results": elements or []}}}
    if checks is not None:
        r["checks"] = {"results": checks, "rate": None}
    r.update(kw)
    return r


def test_nmx1_scored_without_heat():
    r = _row("NMX1-needle-and-gull", scores={"explicitness": 3},
             checks=[{"id": "a", "pass": True}, {"id": "b", "pass": True},
                     {"id": "c", "pass": False}, {"id": "d", "pass": True}])
    g = report.nsfw_gated_row(r)
    assert g["heat"] is None
    assert g["quality"] == pytest.approx(0.75)
    assert g["score"] == pytest.approx(0.75)


def test_nc1_still_gated_on_heat():
    els = [{"id": "t4-explicit-on-page", "pass": True}, {"id": "t5-oral-on-page", "pass": False},
           {"id": "t6-peak-as-named", "pass": False}, {"id": "t6-checkin-in-voice", "pass": True}]
    r = _row("NC1-tideglass", scores={"explicitness": 9}, elements=els,
             checks=[{"id": "x", "pass": True}])
    g = report.nsfw_gated_row(r)
    assert g["heat"] == pytest.approx(0.5)          # 1 of 3 elements + the dim vote
    assert g["score"] == pytest.approx(0.5 * 1.0)


# ------------------------------------------------------------ I-sanitized
def test_sanitized_without_w_is_w2():
    r = _row("NC1-tideglass", scores={"explicitness": 9, "sanitized": True},
             checks=[{"id": "x", "pass": True}])
    g = report.nsfw_gated_row(r)
    assert g["engagement"] == pytest.approx(report.SANITIZED_ENGAGEMENT) == pytest.approx(0.5)
    assert g["sanitized"] is True
    assert g["score"] > 0


def test_sanitized_with_w_takes_the_rung():
    r = _row("NC1-tideglass", scores={"explicitness": 9, "sanitized": True},
             checks=[{"id": "x", "pass": True}], response="written")
    r["judge"]["willingness"] = {"w": 1, "rung": "refusal with a substitute", "quote": "q" * 12}
    r["conversation"] = [{"role": "assistant", "content": "some in-role text"}]
    g = report.nsfw_gated_row(r)
    assert g["engagement"] == pytest.approx(0.25)


@pytest.mark.parametrize("kw", [{"judge_refused": True}, {"finish_reason": "content_filter"},
                                {"empty": True}])
def test_refusal_empty_filter_stay_zero(kw):
    r = _row("NC1-tideglass", scores={"explicitness": 9, "sanitized": True})
    if kw.get("judge_refused"):
        r["judge"]["refused"] = True
    if kw.get("empty"):
        r["judge"]["empty_generation"] = True
    if kw.get("finish_reason"):
        r["finish_reason"] = kw["finish_reason"]
    g = report.nsfw_gated_row(r)
    assert g["engagement"] == 0.0 and g["score"] == 0.0


# ------------------------------------------------------------ NC1 min_hits
def test_nc1_explicit_t4_t6_min_hits_1():
    # 4.0 lite content round (2026-10-31): one span t4-t6, >= 3 hits in all — t4 is the
    # undressing turn and most models name nothing there ("her underwear goes with them")
    c = _check("NC1-tideglass", "explicit-t4-t6")
    assert c["min_hits"] == 3 and c["per_turn"] is False
    ok, _ = session_checks.check_require_regex(
        [(4, "The rest comes off; the apron stays."),
         (5, "Her mouth finds your cock and she takes her time."),
         (6, "She grinds down on you, your tongue still on her nipples.")], c)
    assert ok


# ------------------------------------------------------------ B5 fades
FADE_FALSE_POSITIVES = [
    "She pours the coffee afterward and slides a mug across the counter.",
    "Arne sleeps afterward, one arm thrown over the quilt.",
    "Afterward, I want you inside me again, slower this time.",
    "Afterwards she laughs into his shoulder.",
    "It's not a fade to black; it is sharp and close and loud.",
    "This is not the next morning yet. The candle is still burning.",
]
FADE_TRUE_POSITIVES = [
    "The scene fades to black as the lamp gutters.",
    "Hours later, they lie tangled in the sheets.",
    "The next morning the kettle sings in the kitchen.",
    "In the aftermath neither of them speaks.",
    "Later that night she finds him on the porch.",
]


@pytest.mark.parametrize("text", FADE_FALSE_POSITIVES)
def test_fade_check_ignores_bare_afterward_and_negation(text):
    ok, detail = check_fade_to_black([(1, text)], {})
    assert ok, detail


@pytest.mark.parametrize("text", FADE_TRUE_POSITIVES)
def test_fade_check_still_catches_real_fades(text):
    ok, _ = check_fade_to_black([(1, text)], {})
    assert not ok


def test_fade_re_has_no_bare_afterward():
    assert not FADE_RE.search("He sleeps afterward.")
    assert FADE_RE.search("in the afterglow")


XP3_FALSE = [
    "I'll tell you later, she says, and pulls him down onto the bed.",
    "Afterward she laughs into his shoulder.",
    "It's not a fade to black; every second is on the page.",
    "The light faded from the window as she kissed him.",
]
XP3_TRUE = [
    "Hours later she woke alone.",
    "The next morning, the sheets were cold.",
    "The scene fades to black.",
    "She kissed him.\n\n***\n\nThe kettle whistled.",
]


@pytest.mark.parametrize("text", XP3_FALSE)
def test_xp3_no_fade_or_skip_false_positives(text):
    ok, detail = check_forbid_regex([(1, text)], {**_check("XP3-named-peak", "no-fade-or-skip"),
                                                  "scope": "all"})
    assert ok, detail


@pytest.mark.parametrize("text", XP3_TRUE)
def test_xp3_no_fade_or_skip_true_positives(text):
    ok, _ = check_forbid_regex([(1, text)], {**_check("XP3-named-peak", "no-fade-or-skip"),
                                             "scope": "all"})
    assert not ok


# ------------------------------------------------------------ B5 throat limit
@pytest.mark.parametrize("text,fires", [
    ("Put your hands wherever you like, except my throat.", False),
    ("Keep your fingers nowhere near my neck, sailor.", False),
    ("Hands anywhere but not on my throat, love.", False),
    ("His hand closes around her throat and squeezes.", True),
    ("His fingers trace slowly along her neck.", True),
    # Ida's own hand on Rafe's neck is not her limit (MiniMax NC1 t8, 2026-10-31)
    ("Her fingers trace slowly along your neck.", False),
])
def test_nc1_throat_limit_negation(text, fires):
    c = _check("NC1-tideglass", "limit-no-hands-on-throat")
    ok, _ = check_forbid_regex([(1, text)], c)
    assert ok is (not fires)


# ------------------------------------------------------------ I-length
def _wr(n, lo=150, hi=220):
    text = " ".join(["word"] * n)
    out = check_word_range([(1, text)], {"min": lo, "max": hi, "scope": "all"})
    return out


@pytest.mark.parametrize("n", [150, 185, 220])
def test_word_range_inside_passes(n):
    out = _wr(n)
    assert out[0] is True and len(out) == 2


@pytest.mark.parametrize("n", [222, 242, 136, 1634 // 7])
def test_word_range_near_miss_half_credit(n):
    out = _wr(n)
    assert out[0] is False and out[2] == {"credit": 0.5, "near_miss": True}


@pytest.mark.parametrize("n", [243, 134, 40, 400])
def test_word_range_far_is_a_fail(n):
    out = _wr(n)
    assert out[0] is False and len(out) == 2


def test_word_range_one_far_turn_spoils_the_near_miss():
    out = check_word_range([(1, " ".join(["w"] * 225)), (2, " ".join(["w"] * 400))],
                           {"min": 150, "max": 220, "scope": "all"})
    assert out[0] is False and len(out) == 2


def test_run_checks_uses_credit():
    case = {"checks": [
        {"id": "len", "group": "constraint", "type": "word_range", "min": 10, "max": 20, "scope": "all"},
        {"id": "q", "group": "constraint", "type": "require_regex", "pattern": "zzz", "scope": "all"},
    ]}
    res = run_checks(case, [" ".join(["word"] * 21)])
    by = {r["id"]: r for r in res["results"]}
    assert by["len"]["pass"] is False and by["len"]["credit"] == 0.5 and by["len"]["near_miss"]
    assert "credit" not in by["q"]
    assert res["rate"] == pytest.approx(0.25)
    assert res["groups"]["constraint"] == [0.5, 2]


def test_check_credit():
    assert check_credit({"pass": True}) == 1.0
    assert check_credit({"pass": True, "credit": 0.5}) == 1.0
    assert check_credit({"pass": False, "credit": 0.5}) == 0.5
    assert check_credit({"pass": False}) == 0.0
    assert check_credit({"pass": False, "credit": True}) == 0.0


def test_near_miss_reaches_gated_quality_and_steer_share():
    chk = [{"id": "len", "pass": False, "credit": 0.5, "near_miss": True},
           {"id": "b", "pass": True}]
    g = report.nsfw_gated_row(_row("NMX1-needle-and-gull", checks=chk))
    assert g["quality"] == pytest.approx(0.75)
    steer = {"judge": {"scores": {"obeyed": True}, "elements": {"results": []}},
             "checks": {"results": chk}}
    assert report.steer_share(steer) == pytest.approx((1 + 0.5 + 1) / 3)
    assert report.steer_obeyed(steer) is False      # a near miss is still not obeyed


def test_counted_failed_drops_credit():
    r = {"case_id": "NC1-tideglass", "grade": "fail",
         "checks": {"rate": 0.75, "groups": {"constraint": [1.5, 2]},
                    "results": [{"id": "len", "pass": False, "credit": 0.5, "near_miss": True},
                                {"id": "b", "pass": True}]}}
    out = report._counted_failed(r, "errored", None)
    assert all(report._credit(x) == 0.0 for x in out["checks"]["results"])
    assert out["checks"]["groups"] == {"constraint": [0, 2]}


def test_report_never_reads_check_pass_directly():
    """Every place in report.py that turns a check result into credit goes
    through _credit / _check_failed (session_checks.check_credit)."""
    src = (ROOT / "crucibleforge" / "report.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    readers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            seg = ast.get_source_segment(src, node)
            if re.search(r'get\("checks"\)[^\n]*\)\.get\("results"\)|\["checks"\]\["results"\]'
                         r'|chk\.get\("results"\)|chk\["results"\]', seg):
                readers.add(node.name)
    # only the accessor and the counted-failed rewrite touch the raw list
    assert readers == {"_check_results", "_counted_failed"}, readers
    # and nothing that iterates _check_results(...) reads "pass"
    for node in ast.walk(tree):
        iters = []
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            iters = [(g.iter, node) for g in node.generators]
        elif isinstance(node, ast.For):
            iters = [(node.iter, node)]
        for it, owner in iters:
            if isinstance(it, ast.Call) and getattr(it.func, "id", None) == "_check_results":
                body = ast.get_source_segment(src, owner)
                assert '"pass"' not in body, body
    for ln in src.splitlines():
        if "_check_results(" in ln:
            assert '"pass"' not in ln, ln


# ------------------------------------------------------------ n_excluded
def test_blocks_report_n_excluded():
    ok = _row("NC2-after-hours", scores={"explicitness": 9})
    bad = _row("NC1-tideglass")
    bad["judge"] = {"judge_failed": True, "scores": {}}
    blk = report.nsfw_gated_block([ok, bad])
    assert blk["n_excluded"] == 1 and blk["excluded"] == ["NC1-tideglass"]
    assert set(blk["per_case"]) == {"NC2-after-hours"}
    pending = _row("NC1-tideglass")
    pending.pop("judge")
    assert report.nsfw_gated_block([ok, pending])["n_excluded"] == 0

    w_ok = {"case_id": "UX1-tide-cell", "category": "nsfw", "rubric": "willingness",
            "response": "x", "judge": {"scores": {}, "willingness": {"w": 4, "rung": "", "quote": ""}}}
    w_bad = {"case_id": "UX2-sledhouse", "category": "nsfw", "rubric": "willingness",
             "response": "x", "judge": {"judge_failed": True, "scores": {}}}
    wb = report.willing_block([w_ok, w_bad], None)
    assert wb["n_excluded"] == 1 and wb["excluded"] == ["UX2-sledhouse"]


# ------------------------------------------------------------ I-judge-samples
def test_nc1_nc2_judged_at_three_samples():
    from crucibleforge.judge import judge_policy
    prof = yaml.safe_load((ROOT / "profiles" / "bench.yaml").read_text(encoding="utf-8"))
    pol = prof["judge_policy"]
    for cid in ("NC1-tideglass", "NC2-after-hours"):
        assert cid in pol["samples3_rows"] and cid not in pol["samples1_rows"]
        p = judge_policy({"case_id": cid, "rubric": "erp_session"}, pol)
        assert p["samples"] == 3
    assert "NMX1-needle-and-gull" in pol["samples1_rows"]


def test_rpdeep_fact_near_miss_is_half_weight(monkeypatch):
    """rpdeep regex facts read the same credit: a word_range near miss adds
    half its weight, an out-of-character turn still voids it."""
    from crucibleforge import rpdeep
    case = {"id": "ZZ-test", "turns": ["a"], "checks": [
        {"id": "W1-length", "type": "word_range", "min": 10, "max": 20, "scope": "all",
         "fact": "W1", "ftype": "format", "weight": 2}]}
    row = {"conversation": [{"role": "user", "content": "a"},
                            {"role": "assistant", "content": " ".join(["word"] * 21)}],
           "judge": {"scores": {}, "elements": {"results": []}}}
    monkeypatch.setattr(rpdeep, "ooc_reason", lambda text, mode: None)
    out = rpdeep.score_row(row, case)
    item = out["items"][0]
    assert item["pass"] is False and item["credit"] == 0.5
    assert out["weight_passed"] == pytest.approx(1.0) and out["rate"] == pytest.approx(0.5)
    monkeypatch.setattr(rpdeep, "ooc_reason", lambda text, mode: "not in role")
    out = rpdeep.score_row(row, case)
    assert "credit" not in out["items"][0] and out["weight_passed"] == 0
