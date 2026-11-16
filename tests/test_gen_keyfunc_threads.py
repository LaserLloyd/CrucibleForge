"""keyfunc materialise must be thread-safe (pair-3v 2026-11-14: 2 of 3 L7 units failed with KeyError('id') on the rig).
Cause: _capture() rebound the module-global _prim while another thread's _fits/_apply called it. Same units through a
thread pool and serially must give identical instances and never raise."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from crucibleforge.gen import keyfunc as K

UNITS = [({"type": "keyfunc", "level": lv, "key": f"thr-{lv}-{i}"}, f"KF-T{lv}-{i}") for lv in (6, 7, 8) for i in range(4)]


def _mat(u):
    g, cid = u
    return json.dumps(K.materialize(dict(g), release_seed="t-rel", case_id=cid), sort_keys=True, default=str)


def test_materialise_concurrent_equals_serial(monkeypatch):
    import sys
    K._HIDDEN_CACHE.clear()
    serial = [_mat(u) for u in UNITS]
    monkeypatch.setattr(sys, "getswitchinterval", sys.getswitchinterval)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)          # force interleaving (the rig's 4 runner threads did it by chance)
    try:
        for _ in range(2):
            K._HIDDEN_CACHE.clear()      # a cached instance would hide the race
            with ThreadPoolExecutor(max_workers=6) as ex:
                conc = list(ex.map(_mat, UNITS))
            assert conc == serial
    finally:
        sys.setswitchinterval(old)


def test_capture_does_not_rebind_the_interpreter():
    assert K.__dict__["_prim"].__name__ == "_prim"
    rules = [{"id": "s1", "kind": "add_k", "k": 3}, {"id": "s2", "kind": "mul_k", "k": 2}]
    got = K._capture(rules, [1, 2], "ab", 5, {"s2"})
    assert got == [("s2", (8, (1, 2), "ab"), (16, (1, 2), "ab"))]
    assert K.__dict__["_prim"].__name__ == "_prim"


@pytest.mark.parametrize("lv", (6, 7, 8))
def test_capture_matches_wrapping_oracle(lv, monkeypatch):
    """the new capture walker reports exactly what the old _prim-wrapping capture reported (serially)"""
    import random
    for s in range(6):
        rng = random.Random(s * 31 + lv)
        h = K.generate(s + 100, lv)["_hidden"]
        rules, acc0 = h["rules"], h["acc0"]
        ids = {r["id"] for r in K._hidden_arms(rules)} | {r["id"] for r in rules if r["kind"] not in ("if", "repeat")}
        for _ in range(5):
            xs = [rng.randint(0, 50) for _ in range(rng.randint(0, 6))]
            t = "".join(rng.choice("abcXY1 ") for _ in range(rng.randint(0, 6)))
            got = []
            orig = K._prim

            def wrap(r, st, counters):
                if r["id"] in ids:
                    pre = (st["acc"], tuple(st["xs"]), st["t"])
                    orig(r, st, counters)
                    got.append((r["id"], pre, (st["acc"], tuple(st["xs"]), st["t"])))
                else:
                    orig(r, st, counters)
            monkeypatch.setattr(K, "_prim", wrap)
            K.run(rules, xs, t, acc0)
            monkeypatch.setattr(K, "_prim", orig)
            assert K._capture(rules, xs, t, acc0, ids) == got
