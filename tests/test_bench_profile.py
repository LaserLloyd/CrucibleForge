"""The ONE benchmark (2026-09-23): profiles/bench.yaml is the only profile and
the default everywhere; its case set is the small discriminating subset."""
import json

import pytest

from crucibleforge import cli, config, profiles


def _bench():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile("bench", cfg)
    cfg2, cases = profiles.apply_profile(prof, cfg)
    return prof, cfg2, cases


def test_bench_is_the_only_profile_and_the_default():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    assert profiles.list_profiles(cfg) == ["bench"]
    assert profiles.DEFAULT_PROFILE == "bench"
    # no --profile on the command line -> the bench profile is applied
    args = cli.argparse.Namespace(profile=None, judge=None, smoke=False)
    cfg2, cases, prof = cli._apply_profile_arg(args, cfg)
    assert prof["name"] == "bench" and cfg2["_profile"] == "bench"
    assert cases and args.judge["model_id"].endswith("Qwen3.5-122B-A10B-heretic-v2.i1-Q5_K_M")


def test_bench_budgets_are_unchanged_and_math_skips_recovery():
    prof, cfg2, cases = _bench()
    d = cfg2["defaults"]
    # maintainer 2026-09-23: shrink the test, do not limit models
    assert d["thinking_max_tokens_cap"] == 24576 and d["thinking_max_tokens_factor"] == 4
    assert d["no_recovery"] == ["math"]
    assert d["repeats"]["perf"] == 1
    by = {}
    for c in cases:
        by.setdefault(c["category"], []).append(c)
    assert all(c["max_tokens"] == 6144 for c in by["coding"])
    assert all(c["max_tokens"] == 4096 for c in by["math"] + by["reasoning"])
    j = profiles.profile_judge(prof)
    assert "Qwen3.5-122B" in j["model_id"] and j["thinking"] is True


def test_bench_case_set_is_small_and_drops_the_non_discriminating():
    _, _, cases = _bench()
    ids = {c["id"] for c in cases}
    assert len(ids) == len(cases) == 34
    for dropped in ("CZ01-prime-census", "CZ03-lisp-machine", "MH16-digit-sum-power",
                    "MH22-dual-base-palindrome", "TZ02-unit-disambiguated-toolset",
                    "IZ02-json-single-line-typed", "RH5-weboflies"):
        assert dropped not in ids
    long_cats = {"coding", "math"}
    long_rows = [c for c in cases if c["category"] in long_cats or c["id"] == "RX13-recurrence-term"]
    # the long cases fit in ONE wave on an 8-slot model
    assert len(long_rows) <= 7
    # chat half unchanged (being redesigned separately)
    chat = {c["id"] for c in cases if c["category"] in ("rp", "nsfw", "steer")}
    assert len(chat) == 14


def test_scoring_block_lives_in_the_profile():
    from crucibleforge import report
    sc = report.scoring_config(None)
    assert set(sc) == {"chat", "coding"}
    assert set(sc["chat"]) == {"rp", "nsfw", "explicit_peak", "willing", "steer"}
    assert set(sc["coding"]) == {"coding", "tooluse", "instruct", "reasoning"}


def test_meta_records_profile_revision_and_start(tmp_path, monkeypatch):
    from crucibleforge import runner
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    class P:
        name = "studioforge"
        type = "studioforge"
    entry = {"model_id": "x/y", "name": "lbl"}
    runner._write_meta("lbl", entry, P(), failed=True, error="old", profile="bench")
    runner._write_meta("lbl", entry, P(), profile="bench", revision="3.3.0+abc",
                       reset=True, started="2026-09-23T00:00:00+00:00")
    meta = json.loads((tmp_path / "meta_lbl.json").read_text())
    assert meta["profile"] == "bench" and meta["bench_revision"] == "3.3.0+abc"
    assert meta["started"] == "2026-09-23T00:00:00+00:00"
    assert "error" not in meta and meta["failed"] is False  # reset drops the old run


def test_unknown_profile_still_rejected():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    with pytest.raises(config.ConfigError):
        profiles.load_profile("standard", cfg)


def test_judge_skips_rig_when_nothing_pending(tmp_path, monkeypatch):
    from crucibleforge import judge
    monkeypatch.setattr(config, "results_dir", lambda: tmp_path)
    (tmp_path / "transcripts_lbl.jsonl").write_text(
        json.dumps({"bench_run_id": "b", "case_id": "CZ01", "repeat": 1, "turn": None,
                    "category": "coding", "needs_judge": False}) + "\n")
    assert judge.pending_judge_rows(["lbl"]) == 0
    (tmp_path / "transcripts_lbl.jsonl").write_text(
        json.dumps({"bench_run_id": "b", "case_id": "RP1", "repeat": 1, "turn": None,
                    "category": "rp", "needs_judge": True}) + "\n")
    assert judge.pending_judge_rows(["lbl"]) == 1
