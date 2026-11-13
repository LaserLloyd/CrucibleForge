"""Rows measured under a superseded sampler (2026-11-13).

Nanbeige4.3-33B was benched on server defaults before its card's rule
(``nanbeige4``) was added. A row whose protocol stamp NAMES a sampler other
than the one the model resolves to now is stale: bench-all regenerates it,
and the board never mixes the two sets -- it keeps the old set until the new
one covers every standard job, then switches whole. A row without a sampler
stamp (older harness) is current: no mass regeneration.
"""
from __future__ import annotations

import pytest

from crucibleforge import bench_all, report
from crucibleforge import version as _version

CASES = ["C1", "C2", "C3"]
OLD = {"source": "default", "params": {}}
NEW = {"source": "registry", "params": {"temperature": 0.6, "top_p": 0.95}}
WANT = report._sampler_norm(NEW["params"])


def _row(case, sampler, run="r-old", ts="2026-11-10T00:00:00", **kw):
    r = {"profile": "bench", "case_id": case, "repeat": 1, "grade": "pass",
         "bench_run_id": run, "ts": ts, "category": "x"}
    if sampler is not None:
        r["protocol"] = {"sampler": sampler}
    r.update(kw)
    return r


def _cfg():
    return {"providers": {"sf": {"type": "studioforge"}},
            "models": [{"name": "nb", "model_id": "org/nb-Q8_0", "provider": "sf",
                        "sampler": dict(NEW["params"])},
                       {"name": "p", "model_id": "org/p-Q8_0", "provider": "sf"}]}


# ------------------------------------------------------------- the row rule

def test_only_a_stamped_other_sampler_is_stale():
    assert report.sampler_stale(_row("C1", OLD), WANT)
    assert not report.sampler_stale(_row("C1", NEW), WANT)
    assert not report.sampler_stale(_row("C1", None), WANT)          # older harness
    assert not report.sampler_stale(_row("C1", {"source": "case", "params": {}}), WANT)
    assert not report.sampler_stale(_row("C1", OLD), None)           # unknown: nothing stale
    # 1 == 1.0 and key order do not make a row stale
    w = report._sampler_norm({"temperature": 1, "top_k": 20})
    assert not report.sampler_stale(_row("C1", {"source": "x", "params": {"top_k": 20.0,
                                                                         "temperature": 1.0}}), w)


def test_sampler_want_follows_resolve_sampler(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(report, "_profile_applied",
                        lambda c, name=None: ({}, {"defaults": {"regime": "deployed"}, **c}, []))
    assert report.sampler_want("nb", cfg) == WANT
    assert report.sampler_want("p", cfg) == {}                       # server defaults
    assert report.sampler_want("nope", cfg) is None
    assert report.sampler_want("nb", None) is None
    # a fixed regime sends each case's own sampler: nothing comparable
    monkeypatch.setattr(report, "_profile_applied",
                        lambda c, name=None: ({}, {"defaults": {}, **c}, []))
    assert report.sampler_want("nb", cfg) is None


# ------------------------------------------------------- board: never a mix

def test_board_keeps_the_old_set_until_the_new_one_is_complete():
    old = [_row(c, OLD) for c in CASES]
    half = old + [_row("C1", NEW, run="r-new", ts="2026-11-13T00:00:00")]
    board = report.sampler_select(half, WANT, "board")
    assert board == old                                   # no new row leaks in
    assert report.sampler_select(half, WANT, "current") == [half[-1]]
    full = old + [_row(c, NEW, run="r-new", ts="2026-11-13T00:00:00") for c in CASES]
    assert report.sampler_select(full, WANT, "board") == full[3:]     # switched whole
    assert report.sampler_select(full, WANT, "current") == full[3:]


def test_an_unjudged_new_row_does_not_complete_the_set():
    old = [_row(c, OLD) for c in CASES]
    new = [_row(c, NEW, run="r-new", needs_judge=True,
                judge=({"score": 1} if c != "C3" else None)) for c in CASES]
    assert report.sampler_select(old + new, WANT, "board") == old
    new[2]["judge"] = {"score": 1}
    assert report.sampler_select(old + new, WANT, "board") == new


def test_ladder_rows_do_not_hold_the_switch():
    old = [_row(c, OLD) for c in CASES] + [_row("L1", OLD, ladder_revision="x")]
    new = [_row(c, NEW, run="r-new") for c in CASES]
    assert report.sampler_select(old + new, WANT, "board") == new


def test_unstamped_rows_are_untouched():
    rows = [_row(c, None) for c in CASES] + [_row("C1", NEW, run="r-new")]
    for view in ("board", "current"):
        assert report.sampler_select(rows, WANT, view) == rows


def test_board_filter_applies_the_rule(tmp_path, monkeypatch):
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfgmod.set_results_dir(tmp_path)
    monkeypatch.setattr(_version, "row_current", lambda r, cfg=None: True)
    monkeypatch.setattr(report, "_profile_case_ids", lambda cfg, with_tiers=False: None)
    monkeypatch.setattr(report, "_hidden", lambda cfg: None)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: None)
    monkeypatch.setattr(report, "_profile_case_repeats", lambda cfg: {})
    monkeypatch.setattr(report, "_recheck", lambda rows: rows)
    monkeypatch.setattr(report, "sampler_want", lambda label, cfg: WANT if label == "nb" else None)
    old = [_row(c, OLD) for c in CASES]
    new1 = _row("C1", NEW, run="r-new", ts="2026-11-13T00:00:00")
    rows = old + [new1]
    got = report.board_filter("nb", _cfg(), rows=list(rows))
    assert sorted(r["bench_run_id"] for r in got) == ["r-old"] * 3
    got = report.board_filter("nb", _cfg(), rows=list(rows), sampler_view="current")
    assert [r["bench_run_id"] for r in got] == ["r-new"]


# ------------------------------------------------------ bench-all regenerates

def _plan(cfg, rows):
    return bench_all.plan(cfg, {"org/nb-Q8_0", "org/p-Q8_0"}, board_labels=["nb", "p"],
                          rows_for=lambda l: rows[l], pending_for=lambda l: 0,
                          attempts_for=lambda l: {}, judge_id="j/j", case_ids=CASES,
                          rejudge_for=lambda l: set(), rungs=[], ladder_for=lambda l: None)


def test_bench_all_regenerates_stale_rows_only(monkeypatch):
    monkeypatch.setattr(report, "sampler_want",
                        lambda label, cfg: WANT if label == "nb" else {})
    rows = {"nb": [_row(c, OLD) for c in CASES],
            "p": [_row(c, OLD) for c in CASES]}            # p: stamp == its default
    p = _plan(_cfg(), rows)
    assert p["groups"] == [{"models": ["nb"], "cases": CASES}]
    # resumed half way: only the cases still without a current row
    rows["nb"] = rows["nb"] + [_row("C1", NEW, run="r-new")]
    assert _plan(_cfg(), rows)["groups"] == [{"models": ["nb"], "cases": ["C2", "C3"]}]


def test_unstamped_rows_trigger_nothing(monkeypatch):
    monkeypatch.setattr(report, "sampler_want", lambda label, cfg: WANT)
    rows = {"nb": [_row(c, None) for c in CASES], "p": [_row(c, None) for c in CASES]}
    assert _plan(_cfg(), rows)["groups"] == []


def test_errors_under_the_old_sampler_do_not_use_up_attempts(tmp_path, monkeypatch):
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfgmod.set_results_dir(tmp_path)
    monkeypatch.setattr(_version, "row_current", lambda r, cfg=None: True)
    monkeypatch.setattr(report, "sampler_want", lambda label, cfg: WANT)
    import json
    with (tmp_path / "transcripts_nb.jsonl").open("w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps(_row("C1", OLD, run=f"o{i}", grade="error")) + "\n")
        f.write(json.dumps(_row("C2", NEW, run="n1", grade="error")) + "\n")
    assert bench_all.error_attempts("nb", _cfg()) == {"C2": 1}
