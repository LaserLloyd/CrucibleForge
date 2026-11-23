"""CrucibleForge 4.5 fix pass B (review of WP-05..13b): content-filter and
empty-with-reasoning rows (F7), per-score pending (F7), broken hard file
(F12), legacy tier-3 subsets (F15), judge-spread order (F16), hard file per
score (F18), quote recheck profile gate and flag restore (F1/F22)."""
from __future__ import annotations

import json
import random

import pytest

from crucibleforge import config, profiles, report, scoring45

import test_points_board_39 as pb
import test_wp13b_scoring_20261109 as w13


@pytest.fixture(autouse=True, scope="module")
def _legacy_profile_dir():
    """These tests predate the 4.5 v2 bench45 (hard sets, 29 cases): they run on the
    pre-WP-4 profile kept in tests/fixtures/legacy_profiles/."""
    from crucibleforge import profiles
    mp = pytest.MonkeyPatch()
    legacy = __import__("pathlib").Path(__file__).parent / "fixtures" / "legacy_profiles"
    live = profiles.PROFILES_DIR
    mp.setattr(profiles, "_dirs", lambda cfg: [legacy, live])
    mp.setattr(profiles, "activate_cases_root", lambda prof: None)   # root stays cases/
    yield
    mp.undo()


def _r(**kw):
    r = {"case_id": "C1", "repeat": 1, "turn": None, "category": "nsfw", "grade": "pass",
         "response": "", "finish_reason": "content_filter"}
    r.update(kw)
    return r


# ------------------------------------------------- F7: what is missing

def test_content_filter_is_a_refusal_on_nsfw_and_infrastructure_elsewhere():
    assert scoring45.job_missing_reason([_r()]) is None
    assert scoring45.job_missing_reason([_r(category="rp", case_id="NX9")],
                                        refusal_ids={"NX9"}) is None
    assert scoring45.job_missing_reason([_r(category="coding")]) == "content_filter"
    assert scoring45.job_missing_reason([_r(category="rpdeep")]) == "content_filter"


def test_empty_reply_with_reasoning_is_the_models_own_failure():
    row = _r(category="coding", finish_reason="stop", reasoning="let me think ...")
    assert scoring45.job_missing_reason([row]) is None
    row = _r(category="coding", finish_reason="stop", metrics={"reasoning_tokens": 40})
    assert scoring45.job_missing_reason([row]) is None
    # nothing at all (a dropped stream) is still missing
    assert scoring45.job_missing_reason([_r(category="coding", finish_reason="stop")]) == "empty"


def test_unjudged_nsfw_content_filter_row_stays_missing_until_judged():
    row = _r(needs_judge=True)
    assert scoring45.job_missing_reason([row]) == "unjudged"


# ------------------------------------------- F7: board, per-score pending

NSFW_IDS = ("WL2-lighthouse", "UX1-tide-cell", "UX3-brass-roast")


def _nsfw_only(p):
    """bench45-like: the live NSFW score cut to its Uncensored part."""
    p["scores"]["nsfw"]["parts"] = {"uncensored": {"label": "Uncensored", "short": "Unc",
                                                   "max": 10, "scoring": ["willing"],
                                                   "cases": list(NSFW_IDS)}}
    p["scores"]["nsfw"].pop("hard", None)
    p["cases"]["nsfw"] = list(NSFW_IDS)
    for k in [k for k in p["cases"] if k not in ("nsfw", *pb.WORK)]:
        p["cases"].pop(k)


def _nsfw_row(cid, **kw):
    r = pb._row(cid, "nsfw", 1, False, response="", finish_reason="content_filter",
                needs_judge=True, rubric="nsfw_craft",
                judge={"empty_generation": True, "refused": False, "judge_failed": False,
                       "scores": {}})
    r.pop("grade", None)
    r.update(kw)
    return r


def _env(tmp_path, monkeypatch):
    env = w13._env(tmp_path, monkeypatch)
    prof_p = env.results.parent / "profiles" / "bench.yaml"
    import yaml
    p = yaml.safe_load(prof_p.read_text(encoding="utf-8"))
    _nsfw_only(p)
    prof_p.write_text(yaml.safe_dump(p, sort_keys=False), encoding="utf-8")
    from crucibleforge import version
    orig = version.row_current
    monkeypatch.setattr(version, "row_current",
                        lambda r, *a, **k: True if r.get("category") == "nsfw"
                        else orig(r, *a, **k))
    report._CASES_BY_ID = None
    return env


@pytest.mark.archive_default
def test_content_filtered_nsfw_rows_score_w0_not_missing(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    rows = pb._rows() + w13._hard_rows() + [_nsfw_row(c) for c in NSFW_IDS]
    st = w13._scored(env, {"x": rows})["x"]
    assert not [m for m in st["missing_jobs"] if m["case_id"] in NSFW_IDS]
    sub = st["scorecard"]["subscores"]["nsfw"]
    assert sub["score"] == pytest.approx(0.0) and not sub.get("pending")
    assert st["ranked"] and st["pending_reasons"] == []


@pytest.mark.archive_default
def test_an_errored_nsfw_job_nulls_nsfw_with_a_reason_but_the_model_stays_ranked(
        tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    rows = (pb._rows() + w13._hard_rows() + [_nsfw_row(c) for c in NSFW_IDS[1:]]
            + [_nsfw_row(NSFW_IDS[0], grade="error", error="boom", error_kind="transport",
                         finish_reason="error", judge=None, needs_judge=None)])
    st = w13._scored(env, {"y": rows})["y"]
    sub = st["scorecard"]["subscores"]["nsfw"]
    assert sub["score"] is None and sub["pending"] is True
    assert sub["pending_reasons"] == ["WL2-lighthouse r1: error"]
    assert st["scorecard"]["labels"]["nsfw"] == "-"
    assert st["ranked"] and st["status"] == "ranked"
    assert "WL2-lighthouse r1: error" in st["pending_reasons"]
    assert st["scorecard"]["total"] is not None


@pytest.mark.archive_default
def test_empty_code_reply_with_reasoning_scores_zero_on_the_board(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    rows = [{**r, "response": "", "reasoning": "thinking ...", "finish_reason": "stop",
             "grade": "fail"} if r["category"] == "tooluse" else r
            for r in pb._rows() + w13._hard_rows()] + [_nsfw_row(c) for c in NSFW_IDS]
    st = w13._scored(env, {"z": rows})["z"]
    assert st["missing_jobs"] == []
    assert st["ranked"]
    full = w13._scored(env, {"f": pb._rows() + w13._hard_rows()
                             + [_nsfw_row(c) for c in NSFW_IDS]})["f"]
    assert st["scorecard"]["total"] < full["scorecard"]["total"]


# ---------------------------------------------------- F12: broken hard file

def test_a_broken_declared_hard_file_raises(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    (cases / "hard" / "code").mkdir(parents=True)
    monkeypatch.setattr(config, "CASES_DIR", cases)
    monkeypatch.setattr(profiles, "_HARD_CACHE", {})
    prof = {"scores": {"work": {"parts": {}, "hard": {"max": 2,
                                                     "file": "cases/hard/code/4.5.json"}}}}
    f = cases / "hard" / "code" / "4.5.json"
    with pytest.raises(config.ConfigError):          # declared, absent
        profiles.hard_part(prof, "work")
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        profiles.hard_part(prof, "work")
    f.write_text(json.dumps({"schema": 1}), encoding="utf-8")
    with pytest.raises(config.ConfigError):
        profiles.hard_part(prof, "work")
    f.write_text(json.dumps({"schema": 1, "cases": []}), encoding="utf-8")
    assert profiles.hard_part(prof, "work") is None     # valid and empty: the /8 path


# ------------------------------------------------ F18: hard file per score

def test_hard_file_under_the_wrong_score_or_shared_is_a_problem(tmp_path):
    for s in ("code", "chat", "nsfw"):
        d = tmp_path / "hard" / s
        d.mkdir(parents=True)
        (d / "4.5.json").write_text(json.dumps({"schema": 1, "score": s, "rev": "4.5",
                                                "cases": []}), encoding="utf-8")
    parts = {"a": {"max": 8}}
    prof = {"cases": {}, "scores": {
        "work": {"parts": parts, "hard": {"max": 2, "file": "cases/hard/chat/4.5.json"}},
        "nsfw": {"parts": parts, "hard": {"max": 2, "file": "cases/hard/chat/4.5.json"}},
        "chat": {"parts": parts, "hard": {"max": 2, "file": "cases/hard/chat/4.5.json"}}}}
    errs = profiles.profile_problems(prof, cases_dir=tmp_path)
    assert any("work reads cases/hard/code/" in e for e in errs)
    assert any("nsfw reads cases/hard/nsfw/" in e for e in errs)
    assert sum("already the hard set" in e for e in errs) == 2
    ok = {"cases": {}, "scores": {
        k: {"parts": parts, "hard": {"max": 2, "file": f"cases/hard/{d}/4.5.json"}}
        for k, d in profiles.HARD_SCORE_OF.items()}}
    assert profiles.profile_problems(ok, cases_dir=tmp_path) == []


# ----------------------------------------- F16: judge spread is order-free

def test_judge_sample_spread_does_not_depend_on_row_order():
    a = {"judge": {"elements": {"results": [
        {"id": "x", "n_samples": 3, "verified_samples": [0], "pass": False},
        {"id": "y", "pass": True}]}}}
    b = {"judge": {"elements": {"results": [{"id": "z", "pass": True}]}}}
    c = {"judge": {"elements": {"results": [
        {"id": "u", "n_samples": 3, "verified_samples": [0, 1], "pass": True}]}}}
    rows = [a, b, c]
    want = scoring45.judge_sample_spread(rows)
    rnd = random.Random(3)
    for _ in range(6):
        rnd.shuffle(rows)
        assert scoring45.judge_sample_spread(rows) == want
    assert want["multi_sample_elements"] == 2


# ---------------------------------- F15: legacy tier-3 rows stay out of subsets

def test_legacy_profile_subsets_ignore_tier_3_rows(monkeypatch):
    prof = {"name": "legacy", "cases": {"coding": ["A", "B"]}}
    monkeypatch.setattr(report, "declared_case_sets", lambda p, c: {"s": {"A", "B"}})
    seen = []

    def fake_stats(label, cfg, rows=None, meta=None, era=None):
        seen.append(sorted(r["case_id"] for r in rows or []))
        return {}
    monkeypatch.setattr(report, "model_stats", fake_stats)
    monkeypatch.setattr(report, "_profile_case_ids", lambda *a, **k: None)
    from crucibleforge import tiers
    monkeypatch.setattr(tiers, "row_states", lambda p, b: {})
    by_t = {1: [{"case_id": "A", "category": "coding"}], 2: [],
            3: [{"case_id": "B", "category": "coding"}]}
    st = report.stats_from_tier_rows("m", None, prof, by_t, {}, {"old": False})
    assert "subset_stats" in st
    assert ["A"] in seen and ["A", "B"] not in seen


# ----------------------------- F1/F22: quote recheck is a bench45 switch

def _quote_row(profile):
    return {"case_id": "Q1", "profile": profile, "category": "nsfw", "rubric": "nsfw_craft",
            "response": "She laughed and said the kettle was named Clive.",
            "judge": {"scores": {"sanitized": False, "sanitized_judge": True, "prose": 7},
                      "elements": {"rate": 0.5, "results": [
                          {"id": "e1", "pass": True, "detail": "judge: yes, quote verified"},
                          {"id": "e2", "pass": False, "judge_pass": True,
                           "quote": "the kettle was named Clive",
                           "detail": "judge said yes but its quote is not in the reply"}]}}}


CASE_Q = {"id": "Q1", "rubric": "nsfw_craft",
          "judge_elements": [{"id": "e1"}, {"id": "e2", "dim": "sanitized"}]}


def test_quote_recheck_is_legacy_on_bench_and_full_in_bench45():
    """fix-d (Fable gate WP-4 item 10): bench carries `quote_recheck: legacy` since WP-4 (the
    4.1.0 recheck without the F22 dimension/flag guards, so the 4.1 board stays byte-identical);
    the old 'off on bench' expectation was stale. bench45 runs the guarded recheck."""
    out = report._recheck_quotes(_quote_row("bench"), CASE_Q)
    assert out["judge"]["elements"]["results"][1]["pass"] is True          # legacy: flips the capped flag element too
    assert "sanitized_judge" not in out["judge"]["scores"] or out["judge"]["scores"]["sanitized"] is False
    out = report._recheck_quotes(_quote_row("bench45"), CASE_Q)
    assert out["judge"]["elements"]["results"][1]["pass"] is True
    assert out["judge"]["elements"]["rate"] == 1.0


def test_quote_recheck_restores_the_flag_the_failed_element_had_set():
    """review F22: the element had forced the `sanitized` flag False (the
    judge said True); once it passes the flag reads the judge's value again."""
    out = report._recheck_quotes(_quote_row("bench45"), CASE_Q)
    sc = out["judge"]["scores"]
    assert sc["sanitized"] is True and "sanitized_judge" not in sc


def test_quote_recheck_leaves_a_dimension_capping_element_alone():
    case = {**CASE_Q, "judge_elements": [{"id": "e1"}, {"id": "e2", "dim": "prose", "cap": 3}]}
    row = _quote_row("bench45")
    assert report._recheck_quotes(row, case) == row
