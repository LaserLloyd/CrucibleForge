"""IMPLIED judges re-derived in the 2026-11-14 sweep.

monitor `night` (tier 3): the first sensor_lost had to fall in [a6+3, a6+60), so a monitor that reports a dead sensor after
2 or 3 missing readings failed, though every other sensor-lost check accepts it from the first missing second and the blip
clause already fails an alert on one dropped reading. split x1-x3: the brief returns "each an integer number of cents", but
the judge accepted 1100.3 for 1100 (|r - v| <= .5 on any number)."""
import re

import pytest

from crucibleforge.gen import implied as im


def _seeds(dom, tier, n, v=None):
    out, s = [], 0
    while len(out) < n and s < 300:
        s += 1
        inst = im.make_instance(s, tier, dom)
        if v is None or inst["public"]["params"].get("v") == v:
            out.append(s)
    return out


def _res(inst, code):
    return {r["id"]: r["pass"] for r in im._judge_all(inst, code)["results"]}


@pytest.mark.parametrize("seed", _seeds("monitor", 3, 3))
@pytest.mark.parametrize("after", [2, 3])
def test_monitor_night_accepts_a_short_sensor_lost_delay(seed, after):
    inst = im.make_instance(seed, 3, "monitor")
    code = im.reference_code(inst)
    code2, k = re.subn(r'if s\["lost"\] >= 15 and', f'if s["lost"] >= {after} and', code)
    assert k == 1
    assert _res(inst, code)["night"] and _res(inst, code2)["night"]


@pytest.mark.parametrize("seed", _seeds("split", 1, 4))
def test_split_amounts_must_be_whole_cents(seed):
    inst = im.make_instance(seed, 1, "split")
    fn = inst["public"]["fn"]["main"]
    code = im.reference_code(inst)
    off = code.replace(f"def {fn}(", f"def _ref_{fn}(", 1) + (
        f"\ndef {fn}(*a, **k):\n    r = _ref_{fn}(*a, **k)\n"
        "    return {p: v + 0.3 for p, v in r.items()} if isinstance(r, dict) else r\n")
    ok, bad = _res(inst, code), _res(inst, off)
    assert all(ok.values())
    for x in ("x1", "x2", "x3"):
        if x in bad:
            assert bad[x] is False, x


MOVER = '''
def @FN@(requests, existing, now, tz):
    out = _ref_@FN@(requests, existing, now, tz)
    by = {r["id"]: r for r in requests}
    for o in out:
        want = by[o["id"]].get("start")
        if want is None or o["start"] != want:
            continue
        loc = want + tz
        day, mi = loc // 1440, loc % 1440
        if day % 7 >= 5 or mi < 420 or mi + by[o["id"]]["duration"] > 1200:
            d = day + 1
            while d % 7 >= 5:
                d += 1
            o["start"] = d * 1440 + 540 - tz       # moved to the next weekday 09:00 local: breaks "that time is free, use it"
    return out
'''


def _sched_with(dom_check, n=4):
    out, s = [], 0
    while len(out) < n and s < 400:
        s += 1
        inst = im.make_instance(s, 1, "sched")
        if any(e["id"] == dom_check for e in inst["hidden"]["exps"]):
            out.append(s)
    return out


@pytest.mark.parametrize("check", ["evening", "weekendreq"])
def test_sched_a_requested_free_time_is_used_not_moved(check):
    # brief: "If a request has a "start" and that time is free (nothing else booked then), use it"
    for seed in _sched_with(check):
        inst = im.make_instance(seed, 1, "sched")
        fn = inst["public"]["fn"]["main"]
        code = im.reference_code(inst)
        mover = code.replace(f"def {fn}(", f"def _ref_{fn}(", 1) + MOVER.replace("@FN@", fn)
        ok, bad = _res(inst, code), _res(inst, mover)
        assert ok[check] is True
        if inst["hidden"]["exps"] and _moves(inst, check):
            assert bad[check] is False, (seed, check)


def _moves(inst, check):
    sc = next(e["scen"] for e in inst["hidden"]["exps"] if e["id"] == check)
    want, tz = sc["requests"][0]["start"], sc["tz"] if "tz" in sc else inst["public"]["params"]["tz"]
    loc = want + tz
    return (loc // 1440) % 7 >= 5 or loc % 1440 < 420 or loc % 1440 + sc["requests"][0]["duration"] > 1200


REFUSE_DUP = '''
def @FN@(*a, **k):
    for arg in a:
        if isinstance(arg, list):
            ids = [x.get("id") for x in arg if isinstance(x, dict) and "id" in x]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate id")
    return _ref_@FN@(*a, **k)
'''


@pytest.mark.parametrize("dom", ["returns", "ledger", "expenses"])
def test_sameid_accepts_a_valueerror_refusal_like_its_siblings(dom):
    # pay dupe/otdupe (a duplicate entry: "merge, or raise") and returns zeroqty already accept a ValueError refusal
    seen = 0
    for s in range(1, 200):
        inst = im.make_instance(s, 1, dom)
        if not any(e["id"] == "sameid" for e in inst["hidden"]["exps"]):
            continue
        fn = inst["public"]["fn"]["main"]
        code = im.reference_code(inst)
        ref = _res(inst, code)
        r = _res(inst, code.replace(f"def {fn}(", f"def _ref_{fn}(", 1) + REFUSE_DUP.replace("@FN@", fn))
        assert r["sameid"] is True, (dom, s)
        # only a REPEATED id may be refused: every check without one keeps its verdict
        assert {k: v for k, v in r.items() if k != "sameid"} == {k: v for k, v in ref.items() if k != "sameid"}, (dom, s)
        seen += 1
        if seen == 3:
            break
    assert seen
