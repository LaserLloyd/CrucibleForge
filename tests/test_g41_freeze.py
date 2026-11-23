"""G-41Freeze: the 4.1 `bench` profile reads a frozen case tree (archive/4.1.0/cases),
so 4.5 case edits cannot move the 4.1 board. bench45 keeps cases/."""
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import budget, config, ladder, profiles, version

ROOT = Path(config.ROOT_DIR)
FROZEN = ROOT / "archive" / "4.1.0"


@pytest.fixture(autouse=True)
def _restore():
    yield
    config.set_cases_root(ROOT / "cases")


def test_bench_reads_frozen_tree_and_bench45_reads_live():
    b = profiles.load_profile("bench")
    assert b["cases_dir"] == "archive/4.1.0/cases"
    profiles.activate_cases_root(b)
    assert config.CASES_DIR == FROZEN / "cases"
    assert ladder.LADDER_DIR == FROZEN / "cases" / "ladder"
    assert version._CASES_DIR == FROZEN / "cases"
    assert config.PRIVATE_CASES_DIR == ROOT / "cases" / "private"
    profiles.activate_cases_root(profiles.load_profile("bench45"))
    assert config.CASES_DIR == ROOT / "cases"
    assert ladder.LADDER_DIR == ROOT / "cases" / "ladder"


def test_bench_cases_load_from_frozen_tree():
    profiles.activate_cases_root(profiles.load_profile("bench"))
    cases = config.load_cases(["coding"])
    frozen = json.loads((FROZEN / "cases" / "coding.json").read_text(encoding="utf-8"))
    n = len(frozen)   # ladder packs' inline questions follow the file's own cases
    assert [c["id"] for c in cases][:n] == [c["id"] for c in frozen]
    assert not any(c["id"].startswith("KEYFUNC") for c in cases)


def test_frozen_tree_matches_manifest():
    m = json.loads((FROZEN / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["version"] == "4.1.0" and m["git_commit"].startswith("1671f17")
    on_disk = {str(p.relative_to(FROZEN)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in FROZEN.rglob("*") if p.is_file() and p.name != "MANIFEST.json"}
    assert on_disk == m["files"]


def test_bench45_profile_shape(tmp_path):
    p = profiles.load_profile("bench45")
    assert p["release_seed"] == "4.5.0" and p["ladder"] == {"unlock": 0.9, "rung_stop": 0.5}   # the owner 2026-11-14: "The unlock is .9."
    assert not any("hard" in (s or {}) for s in p["scores"].values())
    w = {k: sum(x["max"] for x in v["parts"].values()) for k, v in p["scores"].items()}
    assert w == {"work": 10, "chat": 10, "nsfw": 10}
    assert p["judge_policy"]["mode"] == "elements"
    # the enforced limit is the run ceiling (R21, 2026-11-14), not a fixed 30 in the budget block
    assert "limit_min" not in p["budget"]["home"] and p["budget"]["reference_models"]
    assert budget.settings(p)["home"]["limit_min"] == p["default_run"]["ceiling_minutes"]


def test_lockout_flag_missing_file_is_not_run():
    fl = {f["key"]: f for f in profiles.flag_columns(profiles.load_profile("bench45"))}
    assert fl["lockout"]["available"] is (ROOT / "cases/ladder/nsfw/uncensored/lockout.json").is_file()
    assert profiles.flag_columns(profiles.load_profile("bench")) == []


@pytest.mark.archive_default
def test_stored_billing_shape_is_off_for_bench_and_on_elsewhere():
    from crucibleforge import preflight
    row = {"grade": "fail", "agent": {"end": "request rejected mid-episode: HTTP 400: credit balance is too low"}}
    assert preflight.billing_error_row({**row, "profile": "bench"}) is False
    assert preflight.billing_error_row({**row, "profile": "bench45"}) is True


def test_board_stats_selects_the_profile_case_tree_before_reading_cases(tmp_path, monkeypatch):
    from crucibleforge import report
    config.set_results_dir(tmp_path)
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench")
    config.set_cases_root(ROOT / "cases")
    report._CASES_BY_ID = {"stale": {}}
    report.board_stats(None, None)
    assert config.CASES_DIR == FROZEN / "cases"


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_verify_cases_accepts_generated_programmatic_cases():
    from crucibleforge import verify_cases as vc
    profiles.activate_cases_root(profiles.load_profile("bench45"))
    cases = [c for c in config.load_cases() if c["id"] in ("TRACEVM-r1-1", "ISTACK-conv-r1-1")]
    assert len(cases) == 2
    for c in cases:
        errs = vc._check(c) + vc._check_judge_elements(c)
        assert not [e for e in errs if "unknown grader" in e or "known rubric" in e], errs


def test_manifest_says_what_the_frozen_tree_is_for():
    m = json.loads((FROZEN / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["frozen_for"] == "bench" and m["excluded"] == []
    assert m["commit"] == m["git_commit"] and m["source_tag"] == "v4.1.0" and m["case_count"] == 388


def test_every_frozen_case_hash_and_grading_hash_equals_1671f17():
    """G-41Freeze condition 4 (Fable gate WP-4): the fixture was generated from `git archive
    1671f17 cases`; an ungated change of what a hash covers (E11a) or an edit of an archived
    case moves one of these and this test fails in seconds instead of the 20-minute board A/B."""
    gold = json.loads((ROOT / "tests" / "fixtures" / "bench41_hashes_1671f17.json").read_text(encoding="utf-8"))
    t = FROZEN / "cases"
    table = version.case_table(t, ladder_dirs=[t / "ladder"])
    assert set(table) == set(gold) and len(gold) == 388
    moved = {k: (gold[k], [v["case"], v["grading"]]) for k, v in table.items()
             if gold[k] != [v["case"], v["grading"]]}
    assert not moved, list(moved.items())[:3]
