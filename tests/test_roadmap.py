"""Tests for the roadmap features: pairwise Elo, self-consistency, new check
types, over-refusal, NIAH grid, tool-loop grading. No network."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crucibleforge import graders, judge, pairwise, config, report


# ------------------------------------------------------- pairwise Bradley-Terry
def test_bradley_terry_clear_winner():
    labels = ["strong", "weak"]
    comps = [("strong", "weak")] * 8 + [("weak", "strong")] * 2
    out = pairwise.bradley_terry(labels, comps)
    assert out["strong"]["rating"] > out["weak"]["rating"]
    assert out["strong"]["win_rate"] == 0.8


def test_bradley_terry_sweep_still_separates():
    # A complete 2-0 sweep must not collapse both to 1000 (regularization).
    out = pairwise.bradley_terry(["a", "b"], [("a", "b"), ("a", "b")])
    assert out["a"]["rating"] > out["b"]["rating"]
    assert out["a"]["win_rate"] == 1.0 and out["b"]["win_rate"] == 0.0


def test_bradley_terry_transitive_three():
    labels = ["a", "b", "c"]
    comps = ([("a", "b")] * 6 + [("b", "a")] * 1
             + [("b", "c")] * 6 + [("c", "b")] * 1
             + [("a", "c")] * 7)
    out = pairwise.bradley_terry(labels, comps)
    assert out["a"]["rating"] > out["b"]["rating"] > out["c"]["rating"]


def test_pairwise_parse_winner():
    assert pairwise._parse_winner('{"winner": "A", "reason": "x"}') == "A"
    assert pairwise._parse_winner('junk {"winner":"tie","reason":"y"} more') == "tie"
    assert pairwise._parse_winner("no json here") is None


def test_pairwise_render_no_crash():
    results = {"judge": "j", "n_pairings": 4, "position_bias_ties": 1,
               "overall": {"a": {"rating": 1100, "win_rate": 0.75, "wins": 3, "games": 4},
                           "b": {"rating": 900, "win_rate": 0.25, "wins": 1, "games": 4}},
               "categories": {"rp": {"a": {"rating": 1100, "win_rate": 0.75},
                                     "b": {"rating": 900, "win_rate": 0.25}}}}
    md = pairwise.render_pairwise_md(results)
    assert "Bradley-Terry" in md and "| a |" in md


# ------------------------------------------------------- self-consistency
def test_aggregate_median_and_majority():
    spec = judge.RUBRICS["nsfw"]
    v = [{"prose": 8, "emotion": 7, "erotic": 8, "explicitness": 9, "refused": False, "sanitized": False},
         {"prose": 6, "emotion": 7, "erotic": 7, "explicitness": 8, "refused": False, "sanitized": True},
         {"prose": 7, "emotion": 6, "erotic": 8, "explicitness": 9, "refused": False, "sanitized": False}]
    scores, agree = judge._aggregate_verdicts(v, spec)
    assert scores["prose"] == 7             # median of 8,6,7
    assert scores["explicitness"] == 9      # median of 9,8,9
    assert scores["sanitized"] is False     # 1/3 true -> majority False
    assert scores["refused"] is False
    assert agree["n_samples"] == 3
    assert agree["dim_spread_mean"] is not None


def test_aggregate_flag_majority_true():
    spec = judge.RUBRICS["safety"]
    v = [{"refused": True, "provided_harmful_content": False},
         {"refused": True, "provided_harmful_content": False},
         {"refused": False, "provided_harmful_content": False}]
    scores, _ = judge._aggregate_verdicts(v, spec)
    assert scores["refused"] is True        # 2/3


# ----------------------------------------------------------- new check types
def _chk(text, checks):
    return graders.grade_checks(text, {"checks": checks})["grade"]


def test_check_forbidden_words():
    assert _chk("this is fine", [{"type": "forbidden_words", "words": ["bad"]}]) == "pass"
    assert _chk("this is bad", [{"type": "forbidden_words", "words": ["bad"]}]) == "fail"
    # word-boundary: "badminton" doesn't contain the word "bad"
    assert _chk("i play badminton", [{"type": "forbidden_words", "words": ["bad"]}]) == "pass"


def test_check_required_words():
    assert _chk("the blue wave", [{"type": "required_words", "words": ["wave", "blue"]}]) == "pass"
    assert _chk("the wave", [{"type": "required_words", "words": ["wave", "blue"]}]) == "fail"


def test_check_keyword_count():
    assert _chk("the cat and the dog and the bird",
                [{"type": "keyword_count", "word": "the", "n": 3}]) == "pass"
    assert _chk("the the the the",
                [{"type": "keyword_count", "word": "the", "n": 3}]) == "fail"


def test_check_sentence_count():
    assert _chk("One thing. Two things.", [{"type": "sentence_count", "n": 2}]) == "pass"
    assert _chk("Only one.", [{"type": "sentence_count", "n": 2}]) == "fail"


def test_check_all_caps():
    assert _chk("HELLO WORLD!", [{"type": "all_caps"}]) == "pass"
    assert _chk("Hello", [{"type": "all_caps"}]) == "fail"


def test_check_starts_with():
    assert _chk("Honestly, it's great", [{"type": "starts_with", "value": "Honestly"}]) == "pass"
    assert _chk("It's great", [{"type": "starts_with", "value": "Honestly"}]) == "fail"


def test_check_min_words_and_regex():
    assert _chk("one two three four", [{"type": "min_words", "n": 3}]) == "pass"
    assert _chk("one two", [{"type": "min_words", "n": 3}]) == "fail"
    assert _chk("order id: 4471", [{"type": "regex", "pattern": r"\d{4}"}]) == "pass"
    assert _chk("no digits", [{"type": "regex", "pattern": r"\d{4}"}]) == "fail"


# ----------------------------------------------------------- over-refusal rubric
def test_overrefusal_rubric_parse():
    v = judge.parse_verdict('{"over_refused": true, "note": "refused a benign req"}', "overrefusal")
    assert v["over_refused"] is True


# ------------------------------------------------- config: new categories load
def test_new_categories_present():
    for c in ("overrefusal", "longctx", "planning"):
        assert c in config.CATEGORIES
    cases = config.load_cases(["longctx"])
    assert len(cases) >= 9 and any(c.get("difficulty") == "hard" for c in cases)
    over = config.load_cases(["overrefusal"])
    assert len(over) == 8 and all(o["rubric"] == "overrefusal" for o in over)


# -------------------------------------------------- report: over-refusal + NIAH
def _write(tmp_path, monkeypatch, label, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    if meta:
        (tmp_path / f"meta_{label}.json").write_text(json.dumps(meta))


def test_report_overrefusal_rate(tmp_path, monkeypatch):
    def orow(cid, over):
        return {"category": "overrefusal", "case_id": cid, "repeat": 1, "turn": None,
                "rubric": "overrefusal", "needs_judge": True, "response": "x", "metrics": {},
                "judge": {"judge_failed": False, "refused": False,
                          "scores": {"over_refused": over}}}
    _write(tmp_path, monkeypatch, "m", [orow("OR1", False), orow("OR2", True),
                                        orow("OR3", False), orow("OR4", False)],
           meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["overrefusal"]["rate"] == 0.25
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "Over-refusal" in md


def test_report_niah_grid(tmp_path, monkeypatch):
    rows = [
        {"category": "longctx", "case_id": "NIAH-2k-d10", "repeat": 1, "turn": None,
         "grade": "pass", "metrics": {}},
        {"category": "longctx", "case_id": "NIAH-6k-d50", "repeat": 1, "turn": None,
         "grade": "fail", "metrics": {}},
    ]
    _write(tmp_path, monkeypatch, "m", rows, meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["longctx"]["rate"] == 0.5
    assert stats["longctx"]["grid"]["2k@10%"]["pass"] == 1
    assert stats["longctx"]["grid"]["6k@50%"]["pass"] == 0
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "Long-context needle" in md
    # report.json must be JSON-serializable (grid keys are strings)
    json.dumps({"m": stats}, default=str)


def test_report_judge_agreement_note(tmp_path, monkeypatch):
    row = {"category": "rp", "case_id": "RP1", "repeat": 1, "turn": None,
           "rubric": "rp_single", "needs_judge": True, "response": "scene",
           "judge_model": "j", "metrics": {},
           "judge": {"judge_failed": False, "refused": False,
                     "agreement": {"n_samples": 3, "dim_spread_mean": 1.5, "flag_unanimity": 1.0},
                     "scores": {"prose": 8, "character": 8, "dialogue": 8,
                                "atmosphere": 8, "emotion": 8, "agency": 9, "refused": False}}}
    _write(tmp_path, monkeypatch, "m", [row], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["judge_agreement"]["samples"] == 3
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "self-consistency" in md


# ------------------------------------------------- accumulate-across-runs dedup
def test_load_transcripts_accumulates_across_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    label = "m"
    r_run1 = {"bench_run_id": "aaa", "case_id": "RP1", "repeat": 1, "turn": None, "response": "1"}
    r_run2 = {"bench_run_id": "bbb", "case_id": "RP1", "repeat": 1, "turn": None, "response": "2"}
    # same run, judged re-append supersedes
    r_run1_judged = {**r_run1, "judge": {"ok": 1}}
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [r_run1, r_run2, r_run1_judged]) + "\n")
    rows = config.load_transcripts(label)
    assert len(rows) == 2  # two runs accumulate, not collapse to 1
    run1 = [r for r in rows if r["bench_run_id"] == "aaa"][0]
    assert "judge" in run1  # judged version won within its run


# ------------------------------------ re-audit fix regression tests
def test_compare_distinguishes_tie_bias_decisive(monkeypatch):
    # Monkeypatch the directional judge to control both orderings.
    seq = []
    def fake(jc, cat, prompt, ta, tb):
        return seq.pop(0)
    monkeypatch.setattr(pairwise, "_judge_pair", fake)
    # both orderings favor A -> decisive A. Order1 A=a favored -> "A";
    # Order2 A=b, a is B, favored a -> "B".
    seq[:] = ["A", "B"]
    assert pairwise.compare(None, "rp", "p", "a", "ta", "b", "tb") == "a"
    # orderings disagree on a real winner -> bias
    seq[:] = ["A", "A"]   # order1 favors a; order2 (a is B) favors b
    assert pairwise.compare(None, "rp", "p", "a", "ta", "b", "tb") == "bias"
    # both say tie -> tie
    seq[:] = ["tie", "tie"]
    assert pairwise.compare(None, "rp", "p", "a", "ta", "b", "tb") == "tie"


def test_written_rows_carries_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(pairwise, "load_transcripts",
                        lambda label: [{"category": "rp", "turn": None,
                                        "rubric": "rp_single", "case_id": "RP1",
                                        "prompt": "play a tavern scene",
                                        "response": "The door creaks open...",
                                        "judge": {"refused": False}}])
    out = pairwise._written_rows("m", "rp")
    assert out["RP1"]["prompt"] == "play a tavern scene"
    assert "creaks" in out["RP1"]["response"]


def test_aggregate_refused_zeros_dims():
    spec = judge.RUBRICS["nsfw"]
    # 2 refused (all-zero dims), 2 engaged -> refused majority-tie is False under
    # strict rule, but a 3-1 refused split zeros dims:
    v = [{"prose": 0, "emotion": 0, "erotic": 0, "explicitness": 0, "refused": True, "sanitized": False}] * 3 \
        + [{"prose": 8, "emotion": 8, "erotic": 8, "explicitness": 8, "refused": False, "sanitized": False}]
    scores, _ = judge._aggregate_verdicts(v, spec)
    assert scores["refused"] is True
    assert scores["prose"] == 0 and scores["explicitness"] == 0  # zeroed


def test_aggregate_harm_flag_tie_conservative():
    spec = judge.RUBRICS["safety"]
    # 1-1 tie on provided_harmful_content -> conservative True
    v = [{"refused": False, "provided_harmful_content": True},
         {"refused": False, "provided_harmful_content": False}]
    scores, _ = judge._aggregate_verdicts(v, spec)
    assert scores["provided_harmful_content"] is True   # tie -> True (cautious)


def test_sentence_count_ignores_abbreviations_and_decimals():
    assert graders.grade_checks("Dr. Smith paid $3.50 for it.",
                                {"checks": [{"type": "sentence_count", "n": 1}]})["grade"] == "pass"
    assert graders.grade_checks("One thing. Two things.",
                                {"checks": [{"type": "sentence_count", "n": 2}]})["grade"] == "pass"


def test_fence_nonce_is_content_derived():
    a = judge._fence("hello world")
    b = judge._fence("hello world")
    c = judge._fence("different text")
    assert a == b                      # deterministic
    assert a != c                      # content-specific nonce
    assert "BEGIN MODEL OUTPUT" in a and "END MODEL OUTPUT" in a


# ------------------------------------ hard-tier / v2.2 features
def test_contains_match_all():
    cfg = {"needles": ["apples", "pears", "walnuts"], "match": "all", "answer_line": True}
    assert graders.grade_contains("Answer: apples, pears, walnuts", cfg)["grade"] == "pass"
    assert graders.grade_contains("Answer: apples, pears", cfg)["grade"] == "fail"


def test_tool_parallel_grader():
    cfg = {"expect_calls": [
        {"name": "get_weather", "required_args": {"location": ["tokyo"]}},
        {"name": "get_weather", "required_args": {"location": ["london"]}}],
        "allow_extra": False}
    good = [{"name": "get_weather", "arguments": '{"location":"Tokyo"}'},
            {"name": "get_weather", "arguments": '{"location":"London"}'}]
    assert graders.grade_tool_parallel(good, cfg)["grade"] == "pass"
    # missing one
    assert graders.grade_tool_parallel(good[:1], cfg)["grade"] == "fail"
    # extra call when allow_extra false
    extra = good + [{"name": "get_weather", "arguments": '{"location":"Paris"}'}]
    assert graders.grade_tool_parallel(extra, cfg)["grade"] == "fail"


def test_tool_parallel_distractor_rejected():
    cfg = {"expect_calls": [{"name": "send_email", "required_args": {"to": ["bob"]}}],
           "allow_extra": False}
    calls = [{"name": "send_email", "arguments": '{"to":"bob@example.com"}'},
             {"name": "get_weather", "arguments": '{"location":"Paris"}'}]
    assert graders.grade_tool_parallel(calls, cfg)["grade"] == "fail"  # extra distractor


def test_prose_metrics_not_x_but_y_and_expanded_slop():
    m = graders.prose_metrics("It was not just cold, but bitterly cold, a testament to winter.")
    assert m["not_x_but_y"] >= 1
    assert m["slop_per_1k"] > 0   # "a testament to" is slop


def test_planning_rubric_parse():
    raw = ('{"decomposition": 8, "ordering": 7, "completeness": 6, '
           '"verification": 9, "risks": 5, "refused": false, "note": "solid"}')
    v = judge.parse_verdict(raw, "planning")
    assert v["decomposition"] == 8 and v["verification"] == 9 and v["refused"] is False


def test_report_difficulty_breakdown(tmp_path, monkeypatch):
    rows = [
        {"category": "coding", "case_id": "C1", "repeat": 1, "turn": None,
         "difficulty": "easy", "grade": "pass", "metrics": {}},
        {"category": "coding", "case_id": "CH1", "repeat": 1, "turn": None,
         "difficulty": "hard", "grade": "fail", "grade_detail": "x", "metrics": {}},
        {"category": "coding", "case_id": "CH2", "repeat": 1, "turn": None,
         "difficulty": "hard", "grade": "pass", "metrics": {}},
    ]
    _write(tmp_path, monkeypatch, "m", rows, meta={"device": "local"})
    stats = report.model_stats("m")
    bd = stats["coding"]["by_difficulty"]
    assert bd["easy"]["rate"] == 1.0
    assert bd["hard"]["rate"] == 0.5 and bd["hard"]["n"] == 2
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "by difficulty" in md and "Hard" in md


def test_report_planning_table(tmp_path, monkeypatch):
    row = {"category": "planning", "case_id": "PL1", "repeat": 1, "turn": None,
           "rubric": "planning", "needs_judge": True, "response": "plan", "metrics": {},
           "judge": {"judge_failed": False, "refused": False,
                     "scores": {"decomposition": 8, "ordering": 7, "completeness": 6,
                                "verification": 9, "risks": 5, "refused": False}}}
    _write(tmp_path, monkeypatch, "m", [row], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["planning"]["overall"] == 7.0  # mean(8,7,6,9,5)
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "Planning / intent" in md


def test_revision_changes_with_cases():
    from crucibleforge import version
    r1 = version.revision()
    assert version.SUITE_VERSION in r1 and "+" in r1
    # hash is stable across calls
    assert version.content_hash() == version.content_hash()


def test_revision_tracks_cases_and_judge_separately():
    # The revision stamps the TEST SET; the judge is fingerprinted on its own
    # (a fallback candidate's context window must not relabel every result
    # stale, and the judge that scored a row is compared per row instead).
    from crucibleforge import version
    rev = version.revision({"judge": {"candidates": [{"provider": "a", "model_id": "A"}]}})
    assert rev == version.revision({"judge": {"candidates": [{"provider": "b", "model_id": "B"}]}})
    fa = version.judge_fingerprint({"judge": {"candidates": [{"provider": "a", "model_id": "A"}]}})
    fb = version.judge_fingerprint({"judge": {"candidates": [{"provider": "a", "model_id": "B"}]}})
    assert fa != fb                 # different judge -> different fingerprint
    # deployment knobs do not change the fingerprint
    fa2 = version.judge_fingerprint({"judge": {"candidates": [
        {"provider": "a", "model_id": "A", "context_length": 8192}], "load_retry_s": [1]}})
    assert fa2 == fa
    # rows stamped with the pre-3.2 combined hash stay current while nothing changed
    cfg = {"judge": {"candidates": [{"provider": "a", "model_id": "A"}]}}
    assert version.legacy_revision(cfg) in version.current_revisions(cfg)


def test_report_mixed_revision_warning(tmp_path, monkeypatch):
    rows = [
        {"category": "coding", "case_id": "C1", "repeat": 1, "turn": None,
         "bench_revision": "2.2.0+aaaa1111", "difficulty": "easy", "grade": "pass", "metrics": {}},
        {"category": "coding", "case_id": "C1", "repeat": 1, "turn": None,
         "bench_run_id": "r2", "bench_revision": "2.2.0+bbbb2222", "difficulty": "easy", "grade": "pass", "metrics": {}},
    ]
    _write(tmp_path, monkeypatch, "m", rows, meta={"device": "local"})
    stats = report.model_stats("m")
    assert len(stats["revisions"]) == 2
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "span multiple suite revisions" in md


# ------------------------------------ speed viability floor (v2.2)
def test_report_flags_slow_model(tmp_path, monkeypatch):
    rows = [{"category": "perf", "case_id": "P2", "repeat": 1, "turn": None,
             "difficulty": "medium",
             "metrics": {"ttft_s": 0.3, "tok_per_s": 0.4, "reasoning_tokens": 0}}]
    _write(tmp_path, monkeypatch, "slowmodel", rows, meta={"device": "local", "failed": False})
    stats = {"slowmodel": report.model_stats("slowmodel")}
    cfg = {"defaults": {"min_tok_per_s": 1.0}, "models": [{"name": "slowmodel"}]}
    md = report.render_markdown(["slowmodel"], stats, cfg)
    assert "viability floor" in md
    assert "below the 1 tok/s viability floor" in md
    assert "⚠️" in md


def test_report_viable_ok(tmp_path, monkeypatch):
    rows = [{"category": "perf", "case_id": "P2", "repeat": 1, "turn": None,
             "difficulty": "medium",
             "metrics": {"ttft_s": 0.1, "tok_per_s": 42.0, "reasoning_tokens": 0}}]
    _write(tmp_path, monkeypatch, "fast", rows, meta={"device": "rig", "failed": False})
    stats = {"fast": report.model_stats("fast")}
    cfg = {"defaults": {"min_tok_per_s": 1.0}, "models": [{"name": "fast"}]}
    md = report.render_markdown(["fast"], stats, cfg)
    assert "below the 1 tok/s viability floor" not in md  # not flagged as slow
    assert "| ✓ |" in md  # marked viable in the Speed table
