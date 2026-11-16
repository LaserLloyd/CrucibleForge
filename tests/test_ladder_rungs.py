"""E2: generated ladder sets (rungs, per_rung, adaptive stop, set share)."""
import argparse
import json

import pytest

from crucibleforge import cli, config, gen, ladder, report
from crucibleforge.profiles import load_profile
from tests.test_gen_registry import _toy_module


@pytest.fixture
def gset(tmp_path, monkeypatch):
    gen.register(_toy_module())
    gen.set_release_seed("R1")
    root = tmp_path / "ladder"
    d = root / "work" / "code"
    d.mkdir(parents=True)
    (d / "set-1.json").write_text(json.dumps(
        {"category": "coding", "generator": {"family": "toy", "rungs": [1, 2, 3, 4, 5],
                                              "per_rung": 2}}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda public=False: [root])
    yield d / "set-1.json"
    gen.unregister("toy")
    gen.set_release_seed(None)


def test_expansion_ids_rungs_and_keys(gset):
    cases = ladder.inline_cases()
    assert [c["id"] for c in cases] == [f"TOY-r{r}-{i}" for r in range(1, 6) for i in (1, 2)]
    assert [c["rung"] for c in cases] == [r for r in range(1, 6) for _ in (1, 2)]
    assert all(c["generator"]["type"] == "toy" and c["generator"]["level"] == c["rung"]
               and c["category"] == "coding" and c["ladder_pack"] == str(gset) for c in cases)
    keys = {c["generator"]["key"] for c in cases}
    assert len(keys) == 10
    assert [c["generator"]["key"] for c in ladder.inline_cases()] == \
        [c["generator"]["key"] for c in cases]                      # deterministic
    gen.set_release_seed("R2")
    assert {c["generator"]["key"] for c in ladder.inline_cases()}.isdisjoint(keys)


def test_cases_load_materialised_with_rung_tag(gset):
    by_id = {c["id"]: c for c in config.load_cases(["coding"])}
    c = by_id["TOY-r3-2"]
    assert c["rung"] == 3 and c["prompt"].startswith("Reply with exactly: ")
    assert by_id["TOY-r1-1"]["prompt"] != by_id["TOY-r1-2"]["prompt"]


def test_discover_carries_rungs(gset):
    prof = load_profile("bench", {})
    spec = ladder.discover(prof)["work/code"][0]
    assert spec["rungs"] == {r: [f"TOY-r{r}-1", f"TOY-r{r}-2"] for r in range(1, 6)}


@pytest.mark.parametrize("shares,want", [
    ({}, [1]),
    ({1: 0.4}, [1]),
    ({1: 0.5}, [1, 2]),
    ({1: 1.0, 2: 0.9}, [1, 2, 3]),
    ({1: 1.0, 2: 0.2, 3: 1.0}, [1, 2]),
    ({r: 1.0 for r in range(1, 6)}, [1, 2, 3, 4, 5]),
])
def test_rungs_due_adaptive_stop(shares, want):
    assert ladder.rungs_due([1, 2, 3, 4, 5], shares, 0.5) == want


def test_set_share_is_mean_over_rungs_unrun_zero():
    r = [1, 2, 3, 4, 5]
    assert ladder.rung_set_share(r, {}) is None
    assert ladder.rung_set_share(r, {1: 1.0, 2: 0.5}) == pytest.approx(0.3)
    assert ladder.rung_set_share(r, {k: 1.0 for k in r}) == 1.0


def test_rung_stop_default_and_profile():
    assert ladder.rung_stop({}) == 0.5
    assert ladder.rung_stop({"ladder": {"rung_stop": 0.7}}) == 0.7


def test_ladder_todo_runs_only_due_rungs(gset, monkeypatch):
    prof = load_profile("bench", {})
    found = ladder.discover(prof)
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, **k: [])
    cfg_p, _, _ = cli._apply_profile_arg(argparse.Namespace(profile=None, judge=None), {})
    prog = {"work/code": {"std": 0.95, "sets": {}, "rungs": {}}}
    cases, jobs, names = cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=prog)
    assert names == ["work/code#1"] and {c["rung"] for c in cases} == {1}
    prog = {"work/code": {"std": 0.95, "sets": {1: 0.1}, "rungs": {1: {1: 0.8}}}}
    cases, _, _ = cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=prog)
    assert {c["rung"] for c in cases} == {1, 2}
    prog = {"work/code": {"std": 0.95, "sets": {1: 0.1}, "rungs": {1: {1: 0.3}}}}
    cases, _, _ = cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=prog)
    assert {c["rung"] for c in cases} == {1}                 # stopped at rung 1
    prog = {"work/code": {"std": 0.5, "sets": {}, "rungs": {}}}
    assert cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=prog)[2] == []   # locked


def test_bad_generated_sets_are_refused(tmp_path):
    f = tmp_path / "set-1.json"
    for bad in ({"generator": {"family": "toy"}},                                # no category
                {"category": "coding", "generator": {"family": "toy", "rungs": []}},
                {"category": "coding", "generator": {"family": "toy", "per_rung": 0}}):
        f.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(config.ConfigError):
            ladder._read_set(f)


# ------------------------------------------------ the board (end to end)

@pytest.mark.archive_default
def test_board_set_share_is_mean_over_rungs(tmp_path, monkeypatch):
    from tests import test_points_board_39 as pb
    gen.register(_toy_module())
    gen.set_release_seed("R1")
    try:
        env = pb._setup(tmp_path, monkeypatch, pb._work_only)
        d = env.ladder / "work" / "code"
        d.mkdir(parents=True)
        (d / "set-1.json").write_text(json.dumps(
            {"category": "coding",
             "generator": {"family": "toy", "rungs": [1, 2, 3, 4, 5], "per_rung": 2}}), encoding="utf-8")
        report._CASES_BY_ID = None
        # rungs 1 and 2 run and pass; rung 3 is DUE but has no row -> the set
        # is pending (not scored): standard points only (H6)
        from crucibleforge import version
        rows = pb._rows() + [pb._row(f"TOY-r{r}-{i}", "coding", rep, False, run="lad",
                                     case_revision=version.case_hash(f"TOY-r{r}-{i}"))
                             for r in (1, 2) for i in (1, 2) for rep in (1, 2)]
        st = pb._board(env, {"m": rows})
        code = st["m"]["scorecard"]["parts"]["work"]["code"]
        assert code == pytest.approx(3)
        det = st["m"]["scorecard"]["ladder"]["work"]["code"]
        assert det["sets"][0]["state"] == "pending" and det["sets"][0]["share"] is None
        assert det["sets"][0]["rung_shares"][1] == pytest.approx(1.0)
        assert det["sets"][0]["rung_shares"][3] is None
    finally:
        gen.unregister("toy")
        gen.set_release_seed(None)
        report._CASES_BY_ID = None


def _h6_board(tmp_path, monkeypatch, rung_rows, blocked=None):
    """Board for a 5-rung generated set; rung_rows = {rung: bad?}."""
    from tests import test_points_board_39 as pb
    from crucibleforge import version
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    d = env.ladder / "work" / "code"
    d.mkdir(parents=True)
    (d / "set-1.json").write_text(json.dumps(
        {"category": "coding", "generator": {"family": "toy", "rungs": [1, 2, 3, 4, 5], "per_rung": 2}}), encoding="utf-8")
    report._CASES_BY_ID = None
    rows = pb._rows() + [pb._row(f"TOY-r{r}-{i}", "coding", rep, bad, run="lad",
                                 case_revision=version.case_hash(f"TOY-r{r}-{i}"))
                         for r, bad in rung_rows.items() for i in (1, 2) for rep in (1, 2)]
    if blocked:
        real = report._drop_billing_jobs
        monkeypatch.setattr(report, "_drop_billing_jobs",
                            lambda prof, by_t: (*real(prof, by_t)[:1], set(blocked), 1))
    return pb._board(env, {"m": rows})["m"]["scorecard"]["ladder"]["work"]["code"]["sets"][0]


@pytest.fixture
def toy(monkeypatch):
    gen.register(_toy_module())
    gen.set_release_seed("R1")
    yield
    gen.unregister("toy")
    gen.set_release_seed(None)
    report._CASES_BY_ID = None


@pytest.mark.archive_default
def test_h6_infra_missing_rung_leaves_the_set_pending(tmp_path, monkeypatch, toy):
    # rung 1 passed, rung 2 is due and has no rows
    s = _h6_board(tmp_path, monkeypatch, {1: False})
    assert s["share"] is None and s["state"] == "pending"


@pytest.mark.archive_default
def test_h6_rung_beyond_the_stop_counts_zero(tmp_path, monkeypatch, toy):
    # rung 1 fails (< stop): rungs 2-5 are skipped by the adaptive stop -> 0
    s = _h6_board(tmp_path, monkeypatch, {1: True})
    assert s["state"] == "scored" and s["share"] == pytest.approx(0.0)
    s = _h6_board(tmp_path / "b", monkeypatch, {1: False, 2: True})
    assert s["state"] == "scored" and s["share"] == pytest.approx(1 / 5)


@pytest.mark.archive_default
def test_h6_blocked_set_stays_none_not_scored(tmp_path, monkeypatch, toy):
    s = _h6_board(tmp_path, monkeypatch, {1: False, 2: True}, blocked={"work/code#1"})
    assert s["share"] is None and s["state"] != "scored"


def test_max_ladder_rounds_covers_twelve_sets():
    assert cli.MAX_LADDER_ROUNDS >= 30
