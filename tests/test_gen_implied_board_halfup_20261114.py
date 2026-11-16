"""IMPLIED leaderboard, variant 2 ("average, rounded to the nearest whole number (halves up)"): the key must round
negative averages correctly. The frozen rc3-main item FLAT-rc2_implied1_L1-002 keyed the `negative` check with
int(avg + 0.5), which truncates toward zero: Cy (-10, -30) -> -19 (true -20), Fay -5 -> -4, Ana -50 -> -49
(scrub review, 2026-11-14). "Halves up" = toward +infinity: -20.5 -> -20, 20.5 -> 21."""
import re

import pytest

from crucibleforge.gen import implied as im
from crucibleforge.gen import _implied_t1 as T

HALF_UP = '''
def _score(xs):
    v = PRM["v"]
    if v == 0: return max(xs)
    if v == 1: return sum(xs)
    s, n = sum(xs), len(xs)
    return (2 * s + n) // (2 * n)
'''
TRUNC = '''
def _score(xs):
    v = PRM["v"]
    if v == 0: return max(xs)
    if v == 1: return sum(xs)
    return int(sum(xs) / len(xs) + 0.5)
'''


def _board_v2_seeds(n=6):
    out, s = [], 0
    while len(out) < n:
        s += 1
        inst = im.make_instance(s, 1, "board")
        if inst["public"]["params"]["v"] == 2:
            out.append(s)
    return out


def _with_score(inst, body):
    code = im.reference_code(inst)
    code2, k = re.subn(r"\ndef _score\(xs\):\n(    .*\n)+", "\n" + body.lstrip("\n"), code, count=1)
    assert k == 1
    return code2


def _res(inst, code):
    return {r["id"]: r["pass"] for r in im._judge_all(inst, code)["results"]}


@pytest.mark.parametrize("xs,want", [([-10, -30], -20), ([-5], -5), ([-50], -50),
                                     ([-20, -21], -20), ([20, 21], 21), ([30, 21], 26), ([-1, 0], 0)])
def test_half_up_rule(xs, want):
    assert T._half_up_avg(xs) == want


@pytest.mark.parametrize("seed", _board_v2_seeds())
def test_negative_check_keys_true_averages(seed):
    inst = im.make_instance(seed, 1, "board")
    ok = _res(inst, _with_score(inst, HALF_UP))
    bad = _res(inst, _with_score(inst, TRUNC))
    assert all(ok.values()), ok
    assert ok["negative"] is True and bad["negative"] is False


@pytest.mark.parametrize("seed", _board_v2_seeds())
def test_reference_is_half_up(seed):
    inst = im.make_instance(seed, 1, "board")
    assert all(_res(inst, im.reference_code(inst)).values())
    assert "int(sum(xs) / len(xs) + 0.5)" not in im.reference_code(inst)


UNROUNDED = '''
def _score(xs):
    v = PRM["v"]
    if v == 0: return max(xs)
    if v == 1: return sum(xs)
    return sum(xs) / len(xs)
'''


@pytest.mark.parametrize("seed", _board_v2_seeds(3))
def test_unrounded_average_is_not_an_int_score(seed):
    # the brief: "score": int, "rounded to the nearest whole number": 25.5 is not 26 (the judge used int(round(x)))
    inst = im.make_instance(seed, 1, "board")
    res = _res(inst, _with_score(inst, UNROUNDED))
    assert res["x3"] is False and res["nones"] is False
    assert _res(inst, _with_score(inst, HALF_UP))["x3"] is True
