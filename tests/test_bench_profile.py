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
    assert cases and args.judge["model_id"].endswith("gemma-4-31B-it-uncensored-heretic-Q8_0")


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
    # chat budgets: 2000 a turn, 3000 a story (x4 for thinking models = 8000 /
    # 12000, under the 24576 cap — the cap never binds on chat)
    assert all(c["max_tokens"] == 2000 for c in by["rp"] + by["nsfw"])
    assert all(c["max_tokens"] == 3000 for c in by["story"])
    j = profiles.profile_judge(prof)
    assert "gemma-4-31B-it-uncensored-heretic" in j["model_id"] and j["thinking"] is True


def test_bench_case_set_is_small_and_drops_the_non_discriminating():
    _, _, cases = _bench()
    ids = {c["id"] for c in cases}
    # 3.5.0 (2026-09-25): 33 + 9 differentiation cases (SX1-3, TZ11-13, RX20-21, RPX3)
    # + 7 NSFW (XP1-3 explicit peak, WL1/WL2 must-write, WL1R/WL2R must-refuse twins)
    assert len(ids) == len(cases) == 49
    for dropped in ("CZ01-prime-census", "CZ03-lisp-machine", "MH16-digit-sum-power",
                    "MH22-dual-base-palindrome", "TZ02-unit-disambiguated-toolset",
                    "IZ02-json-single-line-typed", "RH5-weboflies"):
        assert dropped not in ids
    long_cats = {"coding", "math"}
    long_reason = {"RX13-recurrence-term", "RX20-five-houses-four-attributes",
                   "RX21-flawed-cost-chain"}          # 4096 x4 thinking, like math
    long_rows = [c for c in cases if c["category"] in long_cats or c["id"] in long_reason]
    # 3.5.0: 4 programs + 2 math + RX13/RX20/RX21 = 9 long rows. They NO LONGER
    # fit in one wave on an 8-slot model — the 9th waits for a free slot, which
    # is part of why the 3.5.0 time box is only projected (skill, "Time box").
    assert len(long_rows) == 9
    # chat half (3.4.0 + 3.5.0): 5 RP, 11 NSFW, 2 story, 6 steer = 24 judged rows;
    # the old RP1-4 / RPM1-2 / N1-N4 / NM1 stay in the suite files, out of the bench
    chat = {c["id"] for c in cases if c["category"] in ("rp", "nsfw", "story", "steer")}
    assert chat == {"RPS1-hollow-reach", "RPS2-swap-seats", "RPX1-card-table",
                    "RPX2-user-writes-npc", "RPX3-ferry-at-veln", "NX1-tuning-fork",
                    "NX2-juniper", "NX3-thaw", "NMX1-needle-and-gull",
                    "XP1-three-rooms", "XP2-blindfold", "XP3-named-peak", "WL1-amber",
                    "WL1R-amber-twin", "WL2-lighthouse", "WL2R-lighthouse-twin", "ST1-ten-elements",
                    "ST2-green-door", "S1-sfw-enforce", "S2-stay-in-character", "S3-sfw-pressure",
                    "SX1-harborline-ledger", "SX2-brightdesk-pip", "SX3-kestrel-wren"}
    new_obj = {c["id"] for c in cases if c["category"] in ("tooluse", "reasoning")}
    assert {"TZ11-clinic-ask-then-correct", "TZ12-stock-transfer-pallets",
            "TZ13-meeting-dependent-args", "RX20-five-houses-four-attributes",
            "RX21-flawed-cost-chain"} <= new_obj
    all_ids = {c["id"] for c in config.load_cases(["rp", "nsfw"])}
    assert {"RP1", "RPM2", "N3-graphic", "NM1-escalation"} <= all_ids


def test_scoring_block_lives_in_the_profile():
    from crucibleforge import report
    sc = report.scoring_config(None)
    assert set(sc) == {"chat", "coding"}
    assert sc["chat"] == {"rp": 20, "nsfw": 15, "story": 10, "explicit_peak": 5,
                          "willing": 5, "steer": 5}
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
