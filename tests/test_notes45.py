"""4.5 automatic per-model board notes (zeros, category refusals, abnormalities).

Pure functions over stored rows: every note kind fires on its synthetic fixture
and a clean model gets none. The 4.1 `bench` profile must not change."""
import json
from pathlib import Path

import pytest

from crucibleforge import notes45 as N

PARTS = {
    "NX2-juniper": ("nsfw_writing", "NSFW writing"),
    "XP3-named-peak": ("nsfw_writing", "NSFW writing"),
    "NW1-salt-and-cedar": ("nsfw_writing", "NSFW writing"),
    "NC1-tideglass": ("nsfw_rp", "NSFW RP"),
    "CL5-sheet-rounding": ("code", "Functions"),
    "CL6-number-format": ("code", "Functions"),
    "TZ16-pickup": ("agentic", "Tool use"),
    "RD1-marisol": ("deep_rp", "Deep RP"),
}
CTX = {"parts": PARTS, "reference_judge": "ref-judge", "nominal": {"RD1-marisol": 900}}


def row(cid, share=1.0, **kw):
    r = {"case_id": cid, "repeat": 1, "category": "coding", "score": share,
         "grade": "pass" if (share or 0) >= 1 else "fail", "response": "def f(): return 1",
         "finish_reason": "stop", "metrics": {"completion_tokens": 100, "tok_per_s": 20.0}}
    r.update(kw)
    return r


def kinds(notes):
    return [n["kind"] for n in notes]


def of(notes, kind):
    return [n for n in notes if n["kind"] == kind]


def clean():
    return [row("CL5-sheet-rounding"), row("CL6-number-format"), row("TZ16-pickup"),
            row("RD1-marisol", category="rpdeep", score=None,
                judge={"elements": {"rate": 0.8}}, response="A long enough reply.",
                metrics={"completion_tokens": 900, "tok_per_s": 20.0})]


def test_clean_model_is_silent():
    assert N.build(clean(), CTX) == []


def test_note_shape():
    n = N.build([row("CL5-sheet-rounding", 0.0, grade="fail", response="")], CTX)[0]
    assert set(n) == {"kind", "severity", "part", "cases", "text"}
    assert n["severity"] in ("info", "warn")
    json.dumps(n)


# ---- zeros -------------------------------------------------------------
def test_zero_refusal():
    r = row("NX2-juniper", 0.0, category="nsfw", grade="fail",
            response="I'm sorry, but I can't write that.", judge={"refused": True})
    n = of(N.build([r], CTX), "zero")[0]
    assert "refus" in n["text"] and "NX2" in n["text"] and n["part"] == "nsfw_writing"


def test_zero_empty_truncated_format_code_tool():
    rows = [
        row("CL5-sheet-rounding", 0.0, response="", finish_reason="stop"),
        row("CL6-number-format", 0.0, finish_reason="length", response="def f(:"),
        row("TZ16-pickup", 0.0, category="tooluse",
            elements={"results": [{"id": "step1", "pass": True}, {"id": "step2", "pass": False}]}),
        row("RD1-marisol", 0.0, category="rpdeep", score=None,
            judge={"elements": {"rate": 0.0}}, checks={"rate": 0.0}, response="x",
            judge_gate="format gate on t2: no dialogue"),
    ]
    t = {n["cases"][0]: n["text"] for n in of(N.build(rows, CTX), "zero")}
    assert "empty answer" in t["CL5-sheet-rounding"]
    assert "cut off" in t["CL6-number-format"] or "truncated" in t["CL6-number-format"]
    assert "step2" in t["TZ16-pickup"]
    assert "format gate" in t["RD1-marisol"]


def test_zero_code_compile_vs_wrong_output():
    a = row("CL5-sheet-rounding", 0.0, grade_detail="SyntaxError: invalid syntax (line 3)")
    b = row("CL6-number-format", 0.0, grade_detail="0/6 groups passed")
    t = {n["cases"][0]: n["text"] for n in of(N.build([a, b], CTX), "zero")}
    assert "does not run" in t["CL5-sheet-rounding"]
    assert "wrong output" in t["CL6-number-format"]


def test_zeros_group_per_part_over_three():
    rows = [row(f"C{i}", 0.0, response="") for i in range(5)]
    ctx = {**CTX, "parts": {f"C{i}": ("code", "Functions") for i in range(5)}}
    z = of(N.build(rows, ctx), "zero")
    assert len(z) == 1 and len(z[0]["cases"]) == 5 and "5" in z[0]["text"]


def test_zero_with_infra_cause_is_loud_bug():
    r = row("CL5-sheet-rounding", 0.0, error_kind="transport", error="server error", response="")
    n = of(N.build([r], CTX), "infra_zero")[0]
    assert n["severity"] == "warn" and "BUG" in n["text"]


def test_infra_error_row_is_pending_not_zero():
    r = row("CL5-sheet-rounding", None, grade="error", error_kind="transport", response="")
    del r["score"]
    out = N.build([r], CTX)
    assert "zero" not in kinds(out) and "infra_zero" not in kinds(out)
    assert "pending_rows" in kinds(out)


# ---- category refusals ---------------------------------------------------
def refused(cid):
    return row(cid, 0.0, category="nsfw", grade="fail", response="I can't help with that.",
               judge={"refused": True})


def test_category_refusal_partial():
    rows = [refused("NX2-juniper"), refused("XP3-named-peak"), row("NW1-salt-and-cedar")]
    n = of(N.build(rows, CTX), "refusal")[0]
    assert "2/3" in n["text"] and "NSFW writing" in n["text"]
    assert "NX2" in n["text"] and n["severity"] == "info"


def test_category_refusal_whole_is_stronger():
    rows = [refused("NX2-juniper"), refused("XP3-named-peak"), refused("NW1-salt-and-cedar")]
    n = of(N.build(rows, CTX), "refusal")[0]
    assert n["severity"] == "warn" and "every" in n["text"]


def test_refusal_names_lockout_rung():
    n = of(N.build([refused("NX2-juniper")], {**CTX, "lockout": "hard refusal"}), "refusal")[0]
    assert "hard refusal" in n["text"]


def test_content_filter_counts_as_refusal_and_abnormality():
    r = row("NX2-juniper", 0.0, category="nsfw", finish_reason="content_filter", response="")
    out = N.build([r], CTX)
    assert "refusal" in kinds(out) and "content_filter" in kinds(out)


# ---- abnormalities -------------------------------------------------------
def test_spread_across_repeats():
    rows = [row("CL5-sheet-rounding", 1.0), row("CL5-sheet-rounding", 0.4, repeat=2)]
    assert of(N.build(rows, CTX), "spread")
    ok = [row("CL5-sheet-rounding", 1.0), row("CL5-sheet-rounding", 0.8, repeat=2)]
    assert not of(N.build(ok, CTX), "spread")


def test_judge_failed_pending_and_nonreference():
    rows = clean() + [
        row("NC1-tideglass", None, category="nsfw", judge={"judge_failed": True}),
        row("NX2-juniper", None, category="nsfw", needs_judge=True),
        row("XP3-named-peak", 0.5, category="nsfw", judge={"elements": {"rate": 0.5}},
            judge_model="other-judge"),
    ]
    k = kinds(N.build(rows, CTX))
    assert "judge_failed" in k and "judge_pending" in k and "judge_nonreference" in k


def test_thinking_leak():
    r = row("CL5-sheet-rounding", response="<think>hmm</think> def f(): pass")
    assert of(N.build([r], CTX), "thinking_leak")
    assert not of(N.build([row("CL5-sheet-rounding", response="a < b and b > c")], CTX), "thinking_leak")


def test_repetition_loop():
    span = " ".join(f"w{i}" for i in range(30))
    r = row("RD1-marisol", category="rpdeep", score=None, judge={"elements": {"rate": 0.5}},
            response=f"intro {span} mid {span} more {span} end")
    assert of(N.build([r], CTX), "repetition")
    r2 = {**r, "response": f"intro {span} mid {span} end"}
    assert not of(N.build([r2], CTX), "repetition")


def test_length_outlier_vs_nominal():
    big = row("RD1-marisol", category="rpdeep", score=None, judge={"elements": {"rate": 0.5}},
              metrics={"completion_tokens": 900 * 3 + 1, "tok_per_s": 20.0})
    assert of(N.build([big], CTX), "length_outlier")
    ok = {**big, "metrics": {"completion_tokens": 900 * 3, "tok_per_s": 20.0}}
    assert not of(N.build([ok], CTX), "length_outlier")


def test_tps_outlier_vs_own_median():
    rows = [row(f"C{i}", metrics={"completion_tokens": 10, "tok_per_s": 20.0}) for i in range(6)]
    rows.append(row("CSLOW", metrics={"completion_tokens": 10, "tok_per_s": 3.9}))
    ctx = {**CTX, "parts": {r["case_id"]: ("code", "Functions") for r in rows}}
    n = of(N.build(rows, ctx), "tps_outlier")
    assert n and n[0]["cases"] == ["CSLOW"]
    rows[-1]["metrics"]["tok_per_s"] = 4.5
    assert not of(N.build(rows, ctx), "tps_outlier")


def test_stale_rows():
    out = N.build(clean(), {**CTX, "current": lambda r: r["case_id"] != "CL6-number-format"})
    assert of(N.build(clean(), {**CTX, "current": lambda r: r["case_id"] != "CL6-number-format"}),
              "stale_rows")


def test_whole_part_zero():
    rows = [row("CL5-sheet-rounding", 0.0, response="x"), row("CL6-number-format", 0.0, response="x")]
    assert of(N.build(rows, CTX), "part_all_zero")
    assert not of(N.build([row("CL5-sheet-rounding", 0.0, response="x"),
                           row("CL6-number-format", 1.0)], CTX), "part_all_zero")


def test_perfect_top_rung_is_info_only():
    rows = [row("KF-r5-1", 1.0, rung=5), row("KF-r5-2", 1.0, rung=5), row("KF-r4-1", 1.0, rung=4)]
    ctx = {**CTX, "parts": {"KF-r5-1": ("ladder", "KEYFUNC"), "KF-r5-2": ("ladder", "KEYFUNC"),
                            "KF-r4-1": ("ladder", "KEYFUNC")}}
    n = of(N.build(rows, ctx), "top_rung_perfect")
    assert n and n[0]["severity"] == "info"
    rows[0]["score"] = 0.5
    assert not of(N.build(rows, ctx), "top_rung_perfect")


def test_thresholds_are_named_constants():
    assert N.SPREAD_MIN == 0.5 and N.LOOP_WORDS == 30 and N.LOOP_TIMES == 3
    assert N.LENGTH_FACTOR == 3 and N.TPS_FACTOR == 5 and N.ZEROS_GROUP_OVER == 3


def test_no_row_mutation():
    rows = clean()
    before = json.dumps(rows, sort_keys=True)
    N.build(rows, CTX)
    assert json.dumps(rows, sort_keys=True) == before


# ---- gating + the 4.1 profile --------------------------------------------
def test_settings_gate():
    assert N.enabled({"notes": {"zeros": True, "refusals": True, "anomalies": True}})
    assert not N.enabled({})
    assert not N.enabled({"notes": {"zeros": False, "refusals": False, "anomalies": False}})


def test_bench45_declares_notes():
    from crucibleforge import config, profiles
    p = profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))
    assert p["notes"] == {"zeros": True, "refusals": True, "anomalies": True}
    b = profiles.load_profile("bench", config.load_config(config.EXAMPLE_CONFIG_PATH))
    assert "notes" not in b


def test_bench_profile_notes_unchanged_on_fixture_board():
    """Rows of the 4.1 fixture board: _model_notes under `bench` is identical
    to what it returned before notes45 existed (no notes45 lines, empty list)."""
    from crucibleforge import report
    d = json.loads((Path(__file__).parent / "fixtures/board41/rows.json").read_text(encoding="utf-8"))
    n = 0
    for label, rows in d.items():
        if label.startswith("_"):
            continue
        s = report.model_stats(label, None, rows=rows, meta={})
        assert s["notes45"] == []
        base = [t for t in report._model_notes(label, s, None)]
        s2 = {k: v for k, v in s.items() if k != "notes45"}
        assert base == report._model_notes(label, s2, None)
        n += 1
    assert n >= 4


def test_hook_adds_lines_when_profile_on(monkeypatch):
    from crucibleforge import report
    monkeypatch.setattr(N, "enabled", lambda prof_or_cfg=None: True)
    monkeypatch.setattr(N, "context", lambda cfg=None: CTX)
    rows = [row("CL5-sheet-rounding", 0.0, response="")]
    s = report.model_stats("m", None, rows=rows, meta={})
    assert s["notes45"] and s["notes45"][0]["kind"] == "zero"
    assert any("empty answer" in t for t in report._model_notes("m", s, None))
    json.dumps(s["notes45"])
