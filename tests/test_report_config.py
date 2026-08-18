"""Tests for report aggregation and config loading — no network."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gauntlet import config, report


def _example():
    return config.load_config(config.EXAMPLE_CONFIG_PATH)


def test_config_loads_and_resolves():
    cfg = _example()
    all_enabled = config.resolve_models(cfg, "all")
    assert all_enabled and all(m.get("enabled", True) for m in all_enabled)
    one = config.resolve_models(cfg, "deepseek-flash")
    assert len(one) == 1 and one[0]["name"] == "deepseek-flash"
    assert one[0]["provider"] == "deepseek"


def test_resolve_unknown_model_raises():
    cfg = _example()
    try:
        config.resolve_models(cfg, "does-not-exist")
    except config.ConfigError:
        return
    assert False, "expected ConfigError"


def test_disabled_model_selectable_by_name():
    cfg = _example()
    got = config.resolve_models(cfg, "deepseek-pro")
    assert got[0]["name"] == "deepseek-pro"


def test_all_case_files_load():
    cases = config.load_cases()
    assert len(cases) > 20
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c["category"] in config.CATEGORIES
        assert "max_tokens" in c


def test_smoke_subset_covers_every_category():
    smoke = config.load_cases(smoke=True)
    cats = {c["category"] for c in smoke}
    assert cats == set(config.CATEGORIES), f"smoke missing: {set(config.CATEGORIES) - cats}"


def test_repeats_for_smoke_is_one():
    cfg = _example()
    assert config.repeats_for(cfg, "perf", smoke=True) == 1
    assert config.repeats_for(cfg, "perf", smoke=False) >= 1


def test_load_transcripts_dedupes_last_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    label = "tmodel"
    path = tmp_path / f"transcripts_{label}.jsonl"
    r1 = {"case_id": "X", "repeat": 1, "turn": None, "response": "old"}
    r2 = {"case_id": "X", "repeat": 1, "turn": None, "response": "new", "judge": {"x": 1}}
    path.write_text(json.dumps(r1) + "\n" + json.dumps(r2) + "\n")
    rows = config.load_transcripts(label)
    assert len(rows) == 1
    assert rows[0]["response"] == "new" and "judge" in rows[0]


def test_load_transcripts_skips_torn_line(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    label = "tmodel"
    path = tmp_path / f"transcripts_{label}.jsonl"
    path.write_text(json.dumps({"case_id": "A", "repeat": 1, "turn": None}) + "\n"
                    + '{"case_id": "B", "repeat"')  # torn final line
    rows = config.load_transcripts(label)
    assert len(rows) == 1 and rows[0]["case_id"] == "A"


# ----------------------------------------------------------------- report
def _write(tmp_path, monkeypatch, label, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    if meta:
        (tmp_path / f"meta_{label}.json").write_text(json.dumps(meta))


def test_report_handles_all_none(tmp_path, monkeypatch):
    # A model with only a failed perf row: nothing crashes, everything renders "-".
    _write(tmp_path, monkeypatch, "m", [
        {"category": "perf", "case_id": "P1", "repeat": 1, "turn": None,
         "grade": None, "metrics": {"ttft_s": None, "tok_per_s": None}},
    ], meta={"device": "local", "failed": False})
    stats = report.model_stats("m")
    assert stats["speed"]["tok_per_s_median"] is None
    assert stats["rp"]["overall"] is None
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "| m |" in md and " - " in md


def test_report_pending_judge_flagged(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "m", [
        {"category": "nsfw", "case_id": "N1", "repeat": 1, "turn": None,
         "rubric": "nsfw", "needs_judge": True, "response": "scene",
         "metrics": {}},
    ], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["pending_judge"] == 1
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "NOT yet judged" in md


def test_report_computes_pass_rate(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "m", [
        {"category": "coding", "case_id": "C1", "repeat": 1, "turn": None,
         "grade": "pass", "grade_detail": "ok", "metrics": {}},
        {"category": "coding", "case_id": "C2", "repeat": 1, "turn": None,
         "grade": "fail", "grade_detail": "AssertionError", "metrics": {}},
    ], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["coding"]["rate"] == 0.5
    assert stats["coding"]["passed"] == 1 and stats["coding"]["n"] == 2
    assert any("C2" in f for f in stats["coding"]["failures"])


def test_report_nsfw_ladder_and_willingness(tmp_path, monkeypatch):
    def jrow(case_id, refused, explicit):
        return {"category": "nsfw", "case_id": case_id, "repeat": 1, "turn": None,
                "rubric": "nsfw", "needs_judge": True, "response": "x", "metrics": {},
                "judge": {"judge_failed": False, "refused": refused,
                          "scores": {"prose": 7, "emotion": 7, "erotic": 8,
                                     "explicitness": explicit, "refused": refused,
                                     "sanitized": False}}}
    _write(tmp_path, monkeypatch, "m", [
        jrow("N1", False, 3), jrow("N3", False, 9), jrow("N4", True, 0),
    ], meta={"device": "local"})
    stats = report.model_stats("m")
    assert abs(stats["nsfw"]["willingness"] - 2 / 3) < 1e-6
    assert stats["nsfw"]["per_rung"]["N4"]["refused"] == 1
    assert stats["nsfw"]["per_rung"]["N3"]["explicitness"] == 9


def test_report_safety_metrics(tmp_path, monkeypatch):
    def srow(cid, refused, harmful):
        return {"category": "nsfw", "case_id": cid, "repeat": 1, "turn": None,
                "rubric": "safety", "needs_judge": True, "response": "x", "metrics": {},
                "judge": {"judge_failed": False, "refused": refused,
                          "scores": {"refused": refused,
                                     "provided_harmful_content": harmful}}}
    _write(tmp_path, monkeypatch, "m", [
        srow("FC1", True, False), srow("FC2", False, True),
    ], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["safety"]["refusal_rate"] == 0.5
    assert stats["safety"]["false_compliance"] == 1


# ------------------------------------ audit-fix regression tests (report)
def test_report_sanitized_reads_scores_level(tmp_path, monkeypatch):
    row = {"category": "nsfw", "case_id": "N2", "repeat": 1, "turn": None,
           "rubric": "nsfw", "needs_judge": True, "response": "x", "metrics": {},
           "judge": {"judge_failed": False, "refused": False,
                     "scores": {"prose": 7, "emotion": 6, "erotic": 5,
                                "explicitness": 4, "refused": False,
                                "sanitized": True}}}
    _write(tmp_path, monkeypatch, "m", [row], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["nsfw"]["sanitized"] == 1  # was always 0 before the fix


def test_report_explicitness_peak_vs_mean(tmp_path, monkeypatch):
    def jrow(cid, expl):
        return {"category": "nsfw", "case_id": cid, "repeat": 1, "turn": None,
                "rubric": "nsfw", "needs_judge": True, "response": "x", "metrics": {},
                "judge": {"judge_failed": False, "refused": False,
                          "scores": {"prose": 7, "emotion": 7, "erotic": 7,
                                     "explicitness": expl, "refused": False,
                                     "sanitized": False}}}
    _write(tmp_path, monkeypatch, "m", [jrow("N1", 2), jrow("N3", 10)],
           meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["nsfw"]["explicitness_peak"] == 10
    assert stats["nsfw"]["explicitness_mean"] == 6  # (2+10)/2 — peak != mean


def test_report_steer_rate(tmp_path, monkeypatch):
    def srow(cid, obeyed):
        return {"category": "steer", "case_id": cid, "repeat": 1, "turn": None,
                "rubric": "steer", "needs_judge": True, "response": "x", "metrics": {},
                "judge": {"judge_failed": False, "refused": False,
                          "scores": {"obeyed": obeyed}}}
    _write(tmp_path, monkeypatch, "m", [srow("S1", True), srow("S2", False)],
           meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["steer"]["rate"] == 0.5
    assert stats["steer"]["passed"] == 1 and stats["steer"]["n"] == 2
    assert any("S2" in f for f in stats["steer"]["failures"])


def test_report_empty_generation_excluded(tmp_path, monkeypatch):
    row = {"category": "nsfw", "case_id": "N3", "repeat": 1, "turn": None,
           "rubric": "nsfw", "needs_judge": True, "response": "", "truncated": True,
           "metrics": {}, "judge": {"judge_failed": True, "empty_generation": True,
                                    "refused": False, "scores": None}}
    _write(tmp_path, monkeypatch, "m", [row], meta={"device": "local"})
    stats = report.model_stats("m")
    assert stats["empty_generation"] == 1
    assert stats["judge_failed"] == 0  # empty-gen not counted as a judge parse failure
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "produced NO content" in md


def test_report_family_overlap_note(tmp_path, monkeypatch):
    row = {"category": "rp", "case_id": "RP1", "repeat": 1, "turn": None,
           "rubric": "rp_single", "needs_judge": True, "response": "scene",
           "judge_model": "gemma4-31b-judge", "metrics": {},
           "judge": {"judge_failed": False, "refused": False,
                     "scores": {"prose": 8, "character": 8, "dialogue": 8,
                                "atmosphere": 8, "emotion": 8, "agency": 9,
                                "refused": False}}}
    _write(tmp_path, monkeypatch, "gemma-model", [row], meta={"device": "local"})
    cfg = {"models": [{"name": "gemma-model", "model_id": "some-gemma-4-finetune"}]}
    stats = {"gemma-model": report.model_stats("gemma-model")}
    md = report.render_markdown(["gemma-model"], stats, cfg)
    assert "family overlap" in md
