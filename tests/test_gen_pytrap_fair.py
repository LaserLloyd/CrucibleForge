"""C2 fairness pass on pytrap (2026-11-14): the float-sum repr line carries weight 0; indented replies are not wrong."""
import pytest

from crucibleforge.gen import pytrap as pt

SEED, CASE = "4.5.0", "X"


def _inst_with(tag):
    for f in range(1, 60):
        g = {"type": "pytrap", "level": 1, "ffseed": f}
        m = pt.materialize(g, release_seed=SEED, case_id=CASE)
        if any(tag in s["tags"] for s in m["grader_config"]["snips"]):
            return g, m
    pytest.skip(f"no instance with {tag}")


def test_line_weights_zero_only_the_float_sum_line():
    assert pt.line_weights(["float_repr"] * 3 + ["chained_cmp"] * 3) == [1, 0, 1, 1, 1, 1]
    assert pt.line_weights(["chained_cmp"] * 3) == [1, 1, 1]


def test_float_sum_line_is_free_and_others_are_not():
    g, m = _inst_with("float_repr")
    cfg = m["grader_config"]
    ref = pt.reference(g, release_seed=SEED, case_id=CASE).split("\n")
    # locate the global reply line of the float-sum check
    k, row = 0, None
    for s in cfg["snips"]:
        k += 1                                   # header
        for i, w in enumerate(pt.line_weights(s["tags"])):
            if w == 0 and row is None:
                row = k + i
        k += len(s["true"])
    assert row is not None
    bad = list(ref)
    bad[row] = "0.7"
    r = pt.grade_pytrap("\n".join(bad), cfg)
    assert r["score"] == 1.0 and r["q"] == 1.0
    bad[row - 1] = "zzz"                         # the float_repr line before it still counts
    assert pt.grade_pytrap("\n".join(bad), cfg)["q"] < 1.0


def test_indented_reply_lines_are_accepted():
    g = {"type": "pytrap", "level": 1, "ffseed": 1}
    cfg = pt.materialize(g, release_seed=SEED, case_id=CASE)["grader_config"]
    ref = pt.reference(g, release_seed=SEED, case_id=CASE)
    ind = "\n".join(l if l.startswith("###") else "    " + l for l in ref.split("\n"))
    assert pt.grade_pytrap(ind, cfg)["score"] == 1.0
