"""Suite 3.9 headline (crucibleforge/headline.py, the maintainer 2026-10-28): the
exponential S scale, the case-bootstrap margin of error, paired tie groups
and the "near gate" flag. Runs as a clean clone (conftest)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from crucibleforge import config as cfgmod
from crucibleforge import headline as h
from crucibleforge import profiles, report


@pytest.fixture(scope="module")
def bench():
    cfg = cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile("bench", cfg)
    return SimpleNamespace(cfg=cfg, prof=prof)


# ------------------------------------------------------------------ scale

@pytest.mark.parametrize("p,s", [(0, 0.0), (93, 9.3), (100, 10.0), (125, 10.0 + 2 * (1 - 2 ** -0.5)),
                                 (150, 11.0), (200, 11.5)])
def test_scale_points(p, s):
    assert h.scale(p) == pytest.approx(s)


def test_scale_rounds_to_the_examples():
    assert round(h.scale(93), 1) == 9.3
    assert round(h.scale(125), 1) == 10.6
    assert round(h.scale(150), 1) == 11.0 and round(h.scale(200), 1) == 11.5


def test_scale_is_continuous_monotone_and_harder_above_ten():
    xs = [i * 0.5 for i in range(0, 401)]
    ys = [h.scale(x) for x in xs]
    assert all(b > a for a, b in zip(ys, ys[1:]))
    assert h.scale(100.0001) == pytest.approx(10.0, abs=1e-4)
    # each 50 points above 100 buys half as much as the previous 50
    assert (h.scale(200) - h.scale(150)) == pytest.approx((h.scale(150) - h.scale(100)) / 2)
    assert h.scale(1_000) < 12.0
    for x in (0, 37, 93, 100, 125, 150, 199):
        assert h.unscale(h.scale(x)) == pytest.approx(x)
    assert h.scale(None) is None and h.scale(-5) == 0.0


def test_margin_is_half_the_90_interval_rounded_min_point_one():
    vals = [float(i) / 100 for i in range(101)]          # 0.00 .. 1.00 uniform
    lo, hi = h.interval(vals)
    assert lo == pytest.approx(0.05) and hi == pytest.approx(0.95)
    assert h.margin(vals) == 0.4                          # (0.95 - 0.05) / 2 = 0.45 -> round 0.4
    assert h.margin([7.0] * 50) == 0.1                    # a certain score still says ± 0.1
    assert h.margin([]) is None


def test_format_main_number():
    assert h.fmt_s(9.3, 0.4) == "**9**.3 ± 0.4"
    assert h.fmt_s(11.0) == "**11**.0"
    assert h.fmt_s(9.25, 0.4, html=True) == '<b class="s-main">9</b>.2 ± 0.4' or \
        h.fmt_s(9.25, 0.4, html=True) == '<b class="s-main">9</b>.3 ± 0.4'
    assert h.fmt_s(None) == "-"


# ------------------------------------------------------------- draws

def test_draws_are_reproducible_and_stratified():
    st = {(1, "code"): ["a", "b", "c"], (1, "reason"): ["x", "y"]}
    d1, d2 = h.draws(st, 50, seed=7), h.draws(st, 50, seed=7)
    assert d1 == d2 and h.draws(st, 50, seed=8) != d1
    for m in d1:
        assert sum(len(m.get(i, [])) for i in "abc") == 3
        assert len(m.get("x", [])) + len(m.get("y", [])) == 2


def test_resample_rows_counts_keep_repeats_and_turns_together():
    rows = [{"case_id": "a", "repeat": 1}, {"case_id": "a", "repeat": 2},
            {"case_id": "b", "repeat": 1}, {"case_id": "P1-tiny", "repeat": 1}]
    out = h.resample_rows(rows, {"a": 2}, {"a", "b"})
    assert [(r["case_id"], r["repeat"], r.get("_boot_k")) for r in out] == [
        ("P1-tiny", 1, None), ("a", 1, None), ("a", 2, None), ("a", 1, 1), ("a", 2, 1)]
    assert "_boot_k" not in rows[0]                       # originals untouched


def test_two_level_resample_draws_repeats_inside_a_case():
    """S7: inside a drawn case the repeats are resampled with replacement
    (a session's turns travel with their repeat); seed None = as observed."""
    rows = [{"case_id": "a", "repeat": r, "turn": t, "grade": g}
            for r, g in ((1, "pass"), (2, "fail")) for t in (1, 2)]
    seen = set()
    for seed in range(40):
        out = h.resample_rows(rows, {"a": [seed]}, {"a"})
        assert len(out) == 4
        slots = {}
        for r in out:
            slots.setdefault(r["_boot_r"], set()).add(r["repeat"])
        assert all(len(v) == 1 for v in slots.values())   # a slot = one whole repeat
        seen.add(tuple(sorted((k, next(iter(v))) for k, v in slots.items())))
    assert {((0, 1), (1, 1)), ((0, 2), (1, 2))} <= seen   # both "all pass" and "all fail" occur
    assert h.resample_rows(rows, {"a": [None]}, {"a"}) == rows
    assert h.resample_rows(rows, {"a": [5]}, {"a"}, two_level=False) == rows


def test_bank_stats_counts_a_twice_drawn_task_twice_and_missing_tasks_by_draw():
    rows = [{"case_id": "T1", "grade": "pass", "score": 1.0, "category": "codebank"},
            {"case_id": "T1", "grade": "pass", "score": 1.0, "category": "codebank", "_boot_k": 1},
            {"case_id": "T2", "grade": "fail", "score": 0.0, "category": "codebank"}]
    with report.boot_mode():
        report.boot_set_mult({"T1": 2, "T2": 1, "T3": 2})
        b = report.bank_stats(rows, expected={"T1", "T2", "T3"})
    assert b["tasks"] == 5 and b["score"] == pytest.approx(2 / 5)
    # outside the bootstrap: the normal rule (missing task counts once)
    b = report.bank_stats(rows[:1] + rows[2:], expected={"T1", "T2", "T3"})
    assert b["tasks"] == 3 and b["score"] == pytest.approx(1 / 3)


def test_rpdeep_copies_count_per_draw(monkeypatch):
    from crucibleforge import rpdeep
    rates = {"RD1": 0.2, "RDX": 0.8}
    monkeypatch.setattr(rpdeep, "score_row", lambda r, c, t: {"rate": rates[r["case_id"]], "items": []})
    monkeypatch.setattr(report, "_cases_by_id", lambda: {"RD1": {}, "RDX": {}})
    base = [{"category": "rpdeep", "case_id": c, "repeat": 1, "needs_judge": True,
             "bench_run_id": "r"} for c in rates]
    rows = h.resample_rows(base, {"RD1": 2, "RDX": 1}, {"RD1", "RDX"})
    assert len(rows) == 3
    with report.boot_mode():
        b = report.rpdeep_block(rows, rows)
    assert b["score"] == pytest.approx((0.2 + 0.2 + 0.8) / 3)


