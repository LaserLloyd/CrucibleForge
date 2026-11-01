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
    # 4.0: the CL function set (coding) keeps 6144 x 4 = the 24,576 cap;
    # reasoning/math left the board but keep their budgets for the packs
    assert all(c["max_tokens"] == 6144 for c in by.get("coding", []))
    assert prof["max_tokens"]["reasoning"] == prof["max_tokens"]["math"] == 6144
    # chat budgets: 2000 a turn, 3000 a story (x4 for thinking models = 8000 /
    # 12000, under the 24576 cap — the cap never binds on chat)
    assert all(c["max_tokens"] == 2000 for c in by["rpdeep"] + by["nsfw"])
    assert "story" not in by and "steer" not in by   # step 9: Writing/Character are rpdeep sessions
    j = profiles.profile_judge(prof)
    assert "gemma-4-31B-it-uncensored-heretic" in j["model_id"] and j["thinking"] is True


def test_bench_case_set_is_small_and_drops_the_non_discriminating():
    """Suite 3.9 step 6 (2026-10-29): about one case
    per sub-score point, partial credit inside each case; the cut and parked
    items stay in the case files (some are ladder packs)."""
    _, _, cases = _bench()
    ids = {c["id"] for c in cases}
    # 4.0 lite: 22 + 4 perf probes + the CL function set (cases/coding.json,
    # merged from the coding lane: 0 before, CL3-CL8 after; CL1/CL2 cut for time)
    cl = {i for i in ids if i.startswith("CL")}
    assert len(cl) in (0, 6)
    assert not {i for i in cl if i.startswith(("CL1-", "CL2-"))}
    assert len(ids) == len(cases) == 27 + len(cl)  # 4.0 lite W3: +NC3-palm-house
    for dropped in ("TZ12-stock-transfer-pallets", "TZ13-meeting-dependent-args",
                    "IZ03-no-letter-e-three-lines", "CB-F3-nightwatch-triage",
                    "RX20-five-houses-four-attributes", "RPS1-hollow-reach",
                    "CZ08-triple-substring", "CZ09-range-kth", "TZ01-contradicts-user-assumption",
                    "RX02-knights-knaves", "RX21-flawed-cost-chain", "IZ01-logistics-47-words",
                    # step 8: saturated for MiniMax-M3 (1.0) -> retired
                    "CZ05-optimal-bst-cost", "CZ02-segment-crossings", "CB-D4-watch-sessions",
                    "CB-B5-famcal-rrule", "CB-B6-cron-next", "CB-F8-nudgebot-agent",
                    "TZ06-three-parallel-one-turn", "TZ11-clinic-ask-then-correct",
                    "RX13-recurrence-term", "RX22-six-houses-five-attributes",
                    "MH06-fermat-mod-prime", "MH11-strings-no-adjacent-no-ABC",
                    "MH23-guarded-lattice-paths",
                    "ST1-ten-elements", "NX1-tuning-fork", "S1-sfw-enforce", "RPS2-swap-seats"):
        assert dropped not in ids
    chat = {c["id"] for c in cases if c["category"] in ("rp", "rpdeep", "story", "steer")}
    # step 9: Chat re-calibrated (MiniMax-M3 near half marks); saturated items retired
    assert chat == {"RD1-marisol", "RD4-kettle-saddle", "CC3-tea-hut", "CC4-second-pitch",
                    "RV1-three-at-the-table", "RV2-skarren-hut", "WR1-ninth-string",
                    # 3.10 (review B6): a second standard Writing session
                    "WR2-red-thread-key"}
    for dropped in ("RS1-lantern-ferry", "CC1-porch-light", "CC2-night-shift", "ST2-green-door",
                    "SX1-harborline-ledger", "SX2-brightdesk-pip"):
        assert dropped not in ids
    # 4.0 time budget: NMX1 (near ceiling), WL2R (WL2's twin), UX2 left
    assert {c["id"] for c in cases if c["category"] == "nsfw"} == {
        "WL2-lighthouse", "UX1-tide-cell", "UX3-brass-roast",
        "NC1-tideglass", "NC2-after-hours", "NC3-palm-house",
        "NX2-juniper", "XP3-named-peak", "NW1-salt-and-cedar"}
    assert {c["id"] for c in cases if c["category"] in ("codebank", "codeagent")} == {
        "CB-C5-predict-pyhouse", "CB-C6-predict-node", "CB-F1-larder-bughunt"}
    assert {c["id"] for c in cases if c["category"] in ("tooluse", "reasoning", "math")} == {
        "TZ16-pickup-dst-delay", "TZ19-hotel-cot-deadline", "TZ20-swim-saturday-age-day"}
    assert {c["id"] for c in cases if c["category"] == "coding"} == cl
    for dropped in ("CZ12-interval-ledger", "CZ13-path-glob", "RX24-predict-output-traps",
                    "RX27-calendar-clock-traps", "MH28-derangements-no-successions-7",
                    "CB-C3-predict-sqlite", "NMX1-needle-and-gull"):
        assert dropped not in ids
    all_ids = {c["id"] for c in config.load_cases(["rp", "nsfw"])}
    assert {"RP1", "RPM2", "N3-graphic", "NM1-escalation"} <= all_ids


def test_scoring_block_lives_in_the_profile():
    """Suite 3.9 (the maintainer 2026-10-28): three categories Coding/Work, Chat, NSFW,
    each out of 10 = the sum of its sub-scores' whole-number points; inside a
    sub-score each component weighs its number of standard cases."""
    from crucibleforge import report, tiers
    prof = profiles.load_profile("bench", None)
    defs = {d["key"]: d for d in tiers.scores(prof)}
    assert list(defs) == ["work", "chat", "nsfw"]
    pts = {k: {p["key"]: p["weight"] for p in d["parts"]} for k, d in defs.items()}
    # 4.0 lite: Coding = Functions 5 + Predict & bug hunt 2.5 + Tool use 2.5
    # (keys kept so the ladder packs attach)
    assert pts == {"work": {"code": 5, "reasoning": 2.5, "agentic": 2.5},
                   "chat": {"deep_rp": 3, "conversation": 3, "character": 2, "writing": 2},
                   "nsfw": {"uncensored": 2, "nsfw_rp": 4, "nsfw_writing": 4}}
    assert all(sum(v.values()) == 10 for v in pts.values())
    assert all(len(v) <= 4 for v in pts.values())
    assert [d["in_total"] for d in defs.values()] == [True, True, False]
    sc, pw = report._declared_weights(prof, None, None)
    assert set(sc) == {"work", "chat", "nsfw"}
    # case-count weights: tools TZ16/19/20 = 3; predict C5/C6 2 + bug hunt F1 1;
    # functions = the CL set (0 until the coding lane is merged)
    n_cl = len(prof["cases"].get("coding") or [])
    assert pw["work"] == {"agentic": {"tooluse": 3},
                          "code": {"coding": n_cl},
                          "reasoning": {"codebank": 2, "codeagent": 1}}
    assert pw["chat"] == {"deep_rp": {"rpdeep": 2}, "conversation": {"rpdeep": 2},
                          "character": {"rpdeep": 2}, "writing": {"rpdeep": 2}}
    assert pw["nsfw"] == {"uncensored": {"willing": 3}, "nsfw_rp": {"nsfw_gated": 3},
                          "nsfw_writing": {"nsfw_gated": 3}}
    assert (prof.get("headline") or {}).get("overall") == "sum"


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
    meta = json.loads((tmp_path / "meta_lbl.json").read_text(encoding="utf-8"))
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
                    "category": "coding", "needs_judge": False}) + "\n", encoding="utf-8")
    assert judge.pending_judge_rows(["lbl"]) == 0
    (tmp_path / "transcripts_lbl.jsonl").write_text(
        json.dumps({"bench_run_id": "b", "case_id": "RP1", "repeat": 1, "turn": None,
                    "category": "rp", "needs_judge": True}) + "\n", encoding="utf-8")
    assert judge.pending_judge_rows(["lbl"]) == 1
