"""coding + chat profiles (2026-09-08): each under 30 min, coding judge-free,
both accumulating onto one label."""
import json

from crucibleforge import config, profiles


def _cases(name):
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile(name, cfg)
    cfg2, cases = profiles.apply_profile(prof, cfg)
    return prof, cfg2, cases


def test_coding_profile_is_judge_free_and_deterministic():
    prof, cfg2, cases = _cases("coding")
    assert profiles.profile_judge(prof) is None
    assert {c["category"] for c in cases} == {"perf", "coding", "tooluse", "instruct", "reasoning", "math"}
    # every non-perf case has a deterministic grader — nothing for a judge to do
    assert all(c.get("grader") or c.get("tool_script") for c in cases if c["category"] != "perf")
    assert cfg2["defaults"]["thinking_max_tokens_cap"] == 24576
    assert all(c["max_tokens"] == 6144 for c in cases if c["category"] == "coding")


def test_chat_profile_is_the_judged_half_on_the_122b():
    prof, cfg2, cases = _cases("chat")
    j = profiles.profile_judge(prof)
    assert "Qwen3.5-122B" in j["model_id"] and j["thinking"] is True
    assert {c["category"] for c in cases} == {"perf", "rp", "nsfw", "steer"}


def test_split_profiles_partition_standard():
    _, _, std = _cases("standard")
    _, _, cod = _cases("coding")
    _, _, cha = _cases("chat")
    std_ids = {c["id"] for c in std}
    cod_ids = {c["id"] for c in cod}
    cha_ids = {c["id"] for c in cha}
    perf = {c["id"] for c in std if c["category"] == "perf"}
    assert cod_ids & cha_ids == perf              # only speed probes shared
    assert cod_ids | cha_ids == std_ids           # together = the standard set


def test_meta_accumulates_profiles_and_report_labels_both(tmp_path, monkeypatch):
    from crucibleforge import runner, report
    monkeypatch.setattr(config, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)

    class P:
        name = "studioforge"; type = "studioforge"
    entry = {"model_id": "x/y", "name": "lbl"}
    runner._write_meta("lbl", entry, P(), failed=False, profile="coding")
    runner._write_meta("lbl", entry, P(), failed=False, profile="chat")
    meta = json.loads((tmp_path / "meta_lbl.json").read_text())
    assert meta["profiles"] == ["chat", "coding"] and meta["profile"] == "chat"
    cov = report._coverage([], meta, None)
    assert cov["profile"] == "chat+coding"
    assert "profile chat+coding" in cov["status"]


def test_judge_skips_rig_when_nothing_pending(tmp_path, monkeypatch):
    from crucibleforge import judge, cli
    monkeypatch.setattr(config, "results_dir", lambda: tmp_path)
    (tmp_path / "transcripts_lbl.jsonl").write_text(
        json.dumps({"bench_run_id": "b", "case_id": "CZ01", "repeat": 1, "turn": None,
                    "category": "coding", "needs_judge": False}) + "\n")
    assert judge.pending_judge_rows(["lbl"]) == 0
    (tmp_path / "transcripts_lbl.jsonl").write_text(
        json.dumps({"bench_run_id": "b", "case_id": "RP1", "repeat": 1, "turn": None,
                    "category": "rp", "needs_judge": True}) + "\n")
    assert judge.pending_judge_rows(["lbl"]) == 1
