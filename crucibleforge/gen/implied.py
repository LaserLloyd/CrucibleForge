"""IMPLIED (CrucibleForge port of the fast-fail question ff/q/implied.py, commit 82d7bf6): a short product brief + stated interface; hidden behavioural tests check the unstated things any user of
that product expects. Domains: meeting booker (sched), DJ playlist/mix (dj), baby-monitor alarm (monitor).
Each expectation carries a one-line plain-words justification (WHY ...). Tests accept ranges, not exact constants.
Scoring: q = weighted share of expectations met (explicit rung 0.5 each, implied 1.0 each).
Scenarios are fixed at gen() time and executed inside the sandbox; judging happens outside it."""
import json, random
from . import _implied_more as _M
from . import _implied_t1 as _T
from . import _implied3 as _I3
from . import _implied5_car as _CAR
from . import _implied5_lib as _LIB
from . import _implied5_wh as _WH

DOMS_ALL = ["sched", "dj", "monitor", "meds", "thermo", "split", "backup", "pay", "notify"]
# tier 1 (pocket) draw: monitor and notifier floor every model (alarm-storm / quiet-hours-by-severity are never met), so they
# are kept for tier 3 and bonus use only
DOMS_BASE = ["sched", "dj", "meds", "thermo", "split", "backup", "pay"]
#: tier 1 bank = the base seven + the everyday domains of _implied_t1.py (tier 3 keeps DOMS_ALL, unchanged)
DOMS = DOMS_BASE + list(_T.BUILD)
NEW = set(_M.BUILD) | set(_T.BUILD)
BASE = 10080          # Monday 00:00 UTC of "week 1"
DAY = 1440

DRIVER = r'''
import json, signal, solver
d = json.load(open('in.json'))
class _TO(BaseException): pass
def _h(*a): raise _TO()
signal.signal(signal.SIGALRM, _h)
fns = {k: getattr(solver, v) for k, v in d['fn'].items()}
def jc(x): return json.loads(json.dumps(x))
def run_sched(sc):
    return fns['main'](jc(sc['requests']), jc(sc['existing']), sc['now'], sc['tz'])
def run_dj(sc):
    op = sc['op']
    if op == 'next':
        return fns['next'](list(sc['history']), jc(sc['library']))
    if op == 'multi':
        return [fns['next'](list(h), jc(sc['library'])) for h in sc['cases']]
    if op == 'seq':
        h = list(sc.get('history', []))
        for _ in range(sc['n']):
            h.append(fns['next'](list(h), jc(sc['library'])))
        return h[len(sc.get('history', [])):]
    if op == 'mix':
        return list(fns['mix'](list(sc['a']), list(sc['b']), sc['overlap']))
def run_monitor(sc):
    st = None; outs = []
    for r in sc['readings']:
        st, al = fns['main'](st, dict(r))
        outs.append([str(x) for x in al])
    return outs
def run_calls(sc):
    return [fns[c['f']](*jc(c['a'])) for c in sc['calls']]
RUN = {'sched': run_sched, 'dj': run_dj, 'monitor': run_monitor}.get(d['dom'], run_calls)
out = {}
for k, sc in d['scens'].items():
    signal.alarm(4)
    try:
        out[k] = RUN(sc)
    except BaseException as e:
        out[k] = {'__err': repr(e)[:120]}
    signal.alarm(0)
json.dump(out, open('out.json', 'w'))
'''


def _err(o):
    return isinstance(o, dict) and "__err" in o


# =========================================================== SCHEDULER
def _L(day, h, m, tz):
    return BASE + day * DAY + h * 60 + m - tz


def _lday(u, tz):
    return ((u + tz) // DAY) % 7


def _lmin(u, tz):
    return (u + tz) % DAY


def _norm(out):
    """-> {id: start} or None"""
    if not isinstance(out, list):
        return None
    res = {}
    for it in out:
        if isinstance(it, dict) and "id" in it and "start" in it:
            i, s = it["id"], it["start"]
        elif isinstance(it, (list, tuple)) and len(it) == 2:
            i, s = it
        else:
            return None
        if isinstance(s, bool) or not isinstance(s, (int, float)) or s != s:
            return None
        res[i] = s
    return res


def _ivs(sc, pl):
    d = {r["id"]: r["duration"] for r in sc["requests"]}
    return {i: (s, s + d[i]) for i, s in pl.items() if i in d}


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _all_placed(sc, pl):
    return pl is not None and all(r["id"] in pl for r in sc["requests"])


def _no_overlap(sc, pl):
    iv = list(_ivs(sc, pl).values()) + [(e["start"], e["end"]) for e in sc["existing"]]
    new = list(_ivs(sc, pl).values())
    ex = [(e["start"], e["end"]) for e in sc["existing"]]
    for i, a in enumerate(new):
        for b in new[i + 1:] + ex:
            if _overlap(a, b):
                return False
    return True


def _gap_ok(sc, pl, g=5):
    new = list(_ivs(sc, pl).values())
    ex = [(e["start"], e["end"]) for e in sc["existing"]]
    for i, a in enumerate(new):
        for b in new[i + 1:] + ex:
            if max(a[0] - b[1], b[0] - a[1]) < g:
                return False
    return True


def _hours_ok(sc, pl, ids=None, lo=420, hi=1200):
    tz = sc["tz"]
    for i, (s, e) in _ivs(sc, pl).items():
        if ids is not None and i not in ids:
            continue
        if _lday(s, tz) >= 5 or _lmin(s, tz) < lo or _lmin(s, tz) + (e - s) > hi:
            return False
    return True


def _sj_x1(o, sc, p):
    pl = _norm(o)
    return pl is not None and "r1" in pl and pl["r1"] >= sc["now"]


def _sj_x2(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and _no_overlap(sc, pl)


def _sj_x3(o, sc, p):
    pl = _norm(o)
    return pl is not None and pl.get("r1") == sc["requests"][0]["start"]


def _sj_hours(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and _hours_ok(sc, pl) and all(v >= sc["now"] for v in pl.values())


def _sj_weekend(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and all(_lday(v, sc["tz"]) < 5 and v >= sc["now"] for v in pl.values())


def _sj_past(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and all(v >= sc["now"] for v in pl.values())


def _sj_buffer(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and _gap_ok(sc, pl) and all(v >= sc["now"] for v in pl.values())


def _sj_evening(o, sc, p):
    """a requested free out-of-hours / weekend / late start is booked at the requested time. The brief states it: "If a
    request has a "start" and that time is free (nothing else booked then), use it". (Until 2026-11-14 a move forward into
    working hours within 4 days also passed, after the F-055 panel; the scrub showed that passes a program breaking the
    brief's stated rule, so the stated rule wins.)"""
    pl = _norm(o)
    return pl is not None and pl.get("r1") == sc["requests"][0]["start"]


def _sj_pack(o, sc, p):
    pl = _norm(o)
    return _all_placed(sc, pl) and _no_overlap(sc, pl) and all(
        (v + sc["tz"]) // DAY == (sc["now"] + sc["tz"]) // DAY and v >= sc["now"] for v in pl.values())


def _sj_pack2(o, sc, p):
    pl = _norm(o)
    return (_all_placed(sc, pl) and _no_overlap(sc, pl) and _hours_ok(sc, pl) and all(
        (v + sc["tz"]) // DAY == (sc["now"] + sc["tz"]) // DAY and v >= sc["now"] for v in pl.values()))


def _sj_tzday(o, sc, p):
    pl = _norm(o)
    return (_all_placed(sc, pl) and all(v >= sc["now"] and _lday(v, sc["tz"]) == 0 and _lmin(v, sc["tz"]) >= 420
                                        and _lmin(v, sc["tz"]) + 30 <= 1200 for v in pl.values()))


def _sj_many(o, sc, p):
    pl = _norm(o)
    return (_all_placed(sc, pl) and _no_overlap(sc, pl) and _hours_ok(sc, pl)
            and all(v >= sc["now"] for v in pl.values()))


def _sj_combo(o, sc, p):
    pl = _norm(o)
    if not _all_placed(sc, pl) or not _no_overlap(sc, pl):
        return False
    return (all(v >= sc["now"] for v in pl.values()) and _hours_ok(sc, pl)
            and pl.get("c") == sc["requests"][2]["start"])


def build_sched(rng, tier):
    tz = rng.choice([-480, -300, 0, 60, 330, 540])
    names = rng.choice([("schedule", "book"), ("book_meetings", "book"), ("plan_meetings", "book")])
    fn = names[0]
    S = {}  # id -> (scen, judge, weight, kind, why)

    def add(i, sc, j, w, kind, why):
        S[i] = {"scen": {**sc, "tz": sc.get("tz", tz)}, "w": w, "kind": kind, "why": why}

    d = rng.randint(1, 2)
    add("x1", {"now": _L(d, 9, 0, tz), "requests": [{"id": "r1", "duration": 30}], "existing": []}, None, .25, "x",
        "the request gets booked")
    ex = [{"start": _L(1, 9, 0, tz), "end": _L(1, 12, 0, tz)}, {"start": _L(1, 13, 0, tz), "end": _L(1, 15, 0, tz)},
          {"start": _L(2, 9, 0, tz), "end": _L(2, 17, 0, tz)}]
    add("x2", {"now": _L(1, 8, 0, tz), "existing": ex, "requests": [
        {"id": "r1", "duration": 60}, {"id": "r2", "duration": 45, "start": _L(1, 10, 0, tz)},
        {"id": "r3", "duration": 90}, {"id": "r4", "duration": 30}]}, None, .25, "x", "never double-book")
    dd, hh = rng.randint(1, 3), rng.randint(10, 15)
    add("x3", {"now": _L(1, 8, 0, tz), "existing": [{"start": _L(1, 9, 0, tz), "end": _L(1, 10, 0, tz)}],
               "requests": [{"id": "r1", "duration": 45, "start": _L(dd + 1, hh, 0, tz)}]}, None, .25, "x",
        "a free time the person asked for is used")
    add("hours", {"now": _L(1, rng.choice([2, 3, 4]), 30, tz), "requests": [{"id": "r1", "duration": 60}],
                  "existing": []}, None, 1, "i", "nobody wants a 3am meeting: default to working hours")
    add("weekend", {"now": _L(5, rng.randint(9, 15), 0, tz), "requests": [{"id": "r1", "duration": 30}], "existing": []},
        None, 1, "i", "a meeting asked for on the weekend lands on a weekday")
    add("eod", {"now": _L(2, 19, 15, tz), "requests": [{"id": "r1", "duration": 60}], "existing": []}, None, 1, "i",
        "a meeting must not run past the end of the working day")
    add("past", {"now": _L(2, 11, 0, tz), "requests": [{"id": "r1", "duration": 30, "start": _L(2, 9, 0, tz)}],
                 "existing": []}, None, 1, "i", "you cannot book a meeting that already happened")
    add("buffer", {"now": _L(2, 9, 0, tz), "existing": [{"start": _L(2, 9, 0, tz), "end": _L(2, 10, 0, tz)}],
                   "requests": [{"id": "r1", "duration": 30}, {"id": "r2", "duration": 30}]}, None, 1, "i",
        "people need a few minutes between back-to-back meetings")
    ev = rng.choice([(rng.randint(1, 3), 20, 0), (5, 10, 0)])
    add("evening", {"now": _L(0, 9, 0, tz), "requests": [{"id": "r1", "duration": 30, "start": _L(ev[0], ev[1], ev[2], tz)}],
                    "existing": []}, None, 1, "a", "if I explicitly ask for a time (even evening/weekend) I get it")
    add("weekendreq", {"now": _L(0, 9, 0, tz), "requests": [{"id": "r1", "duration": 30, "start": _L(5, rng.randint(9, 15), 0, tz)}],
                       "existing": []}, None, 1, "a", "a weekend time I ask for is mine, not moved to Monday")
    add("latestart", {"now": _L(0, 9, 0, tz), "requests": [{"id": "r1", "duration": 45, "start": _L(rng.randint(1, 3), 17, 30, tz)}],
                      "existing": []}, None, 1, "a", "a late-afternoon time I ask for is honoured too")
    # tier 3 extras
    tz2 = rng.choice([540, 600])
    add("tzday", {"tz": tz2, "now": _L(0, 7, 0, tz2), "requests": [{"id": "r1", "duration": 30}], "existing": []}, None, 1,
        "i3", "my Monday is my local Monday, not UTC's Sunday")
    add("many", {"now": _L(3, 15, 0, tz), "requests": [{"id": f"m{i}", "duration": 60} for i in range(10)], "existing": []},
        None, 1.5, "i3", "a long queue spills over to later weekdays, still inside working hours, never on the weekend")
    add("combo", {"now": _L(4, 17, 40, tz), "existing": [
        {"start": _L(7, 9, 0, tz), "end": _L(7, 12, 0, tz)}, {"start": _L(7, 13, 0, tz), "end": _L(7, 17, 0, tz)}],
        "requests": [{"id": "a", "duration": 60}, {"id": "b", "duration": 45},
                     {"id": "c", "duration": 30, "start": _L(9, 10, 0, tz)},
                     {"id": "d", "duration": 30, "start": _L(7, 10, 0, tz)}]}, None, 1.5, "i3",
        "late Friday plus a taken Monday: everything lands in working hours on a weekday, and the free time asked for is honoured")
    t1_pool = ["hours", "weekend", "eod", "evening", "weekendreq"]
    text = (f"Build a meeting booker for one person: it manages a team's WORK calendar.\n\n    def {fn}(requests, existing, now, tz) -> list\n\n"
            "- requests: [{\"id\": str, \"duration\": minutes, \"start\": optional minute}]; existing: [{\"start\": m, \"end\": m}] "
            "(the person's calendar; an interval covers start up to, but not including, end); now: the current time; tz: the person's UTC offset in minutes (local time = UTC + tz).\n"
            "- All times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes).\n"
            "- Book every request. If a request has a \"start\" and that time is free (nothing else booked then), use it; otherwise book "
            "the next time that works. Return [{\"id\": ..., \"start\": ...}] (end = start + duration).")
    if tier == 3:
        text += "\n- It is used by a real calendar app: queues can be long and span weeks, and people are in many time zones."
    judges = {"x1": _sj_x1, "x2": _sj_x2, "x3": _sj_x3, "hours": _sj_hours, "weekend": _sj_weekend, "eod": _sj_hours,
              "past": _sj_past, "buffer": _sj_buffer, "evening": _sj_evening, "weekendreq": _sj_evening, "latestart": _sj_evening, "tzday": _sj_tzday,
              "many": _sj_many, "combo": _sj_combo}
    return {"text": text, "fn": {"main": fn}, "S": S, "t1_pool": t1_pool, "t3_extra": ["tzday", "many", "combo"],
            "params": {"tz": tz}, "judges": judges}


# =========================================================== DJ
def _ids(lib):
    return [t["id"] for t in lib]


def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _okmix(o):
    return isinstance(o, list) and all(_isnum(x) for x in o)


def _dj_x1(o, sc, p):
    return not _err(o) and o in _ids(sc["library"])


def _dj_x2(o, sc, p):
    if not _okmix(o) or len(o) != len(sc["a"]) + len(sc["b"]) - sc["overlap"]:
        return False
    m = o[len(sc["a"]) - sc["overlap"] // 2]
    return abs(m - 0.3) > 0.01 and abs(m - 0.2) > 0.01


def _dj_x3(o, sc, p):
    return _okmix(o) and len(o) == len(sc["a"]) + len(sc["b"]) and all(abs(x - y) < 1e-9 for x, y in zip(o, sc["a"] + sc["b"]))


def _dj_norepeat(o, sc, p):
    return isinstance(o, list) and all(r in _ids(sc["library"]) and r != h[-1] for r, h in zip(o, sc["cases"]))


def _dj_round(o, sc, p):
    L = len(sc["library"])
    if not isinstance(o, list) or len(o) != sc["n"] or any(x not in _ids(sc["library"]) for x in o):
        return False
    return all(len(set(o[i:i + L])) == L for i in range(0, len(o) - L + 1, L))


def _dj_single(o, sc, p):
    return isinstance(o, list) and all(r == sc["library"][0]["id"] for r in o)


def _dj_allplayed(o, sc, p):
    return isinstance(o, list) and len(o) == len(sc["cases"]) and all(
        r in _ids(sc["library"]) and r != h[-1] for r, h in zip(o, sc["cases"]))


def _dj_tempo(o, sc, p):
    """a song of similar tempo plays next; the song that just played is not a next song (scrub pass-1: replaying it, a
    0 bpm step, passed)"""
    bpm = {t["id"]: t["bpm"] for t in sc["library"]}
    return not _err(o) and o in bpm and o != sc["history"][-1] and abs(bpm[o] - bpm[sc["history"][-1]]) <= 10


def _dj_unknown(o, sc, p):
    return not _err(o) and o in _ids(sc["library"]) and o != sc["history"][-1]


def _dj_empty(o, sc, p):
    return not _err(o) and not o


def _dj_clip(o, sc, p):
    return _okmix(o) and len(o) == len(sc["a"]) + len(sc["b"]) - sc["overlap"] and max(abs(x) for x in o) <= 1.0000001


def _dj_fade(o, sc, p):
    if not _okmix(o) or len(o) != len(sc["a"]) + len(sc["b"]) - sc["overlap"]:
        return False
    s = len(sc["a"]) - sc["overlap"]
    return o[s] > 0.3 and o[s + sc["overlap"] - 1] < -0.3


def _dj_keepvol(o, sc, p):
    a, b, n = sc["a"], sc["b"], sc["overlap"]
    if not _okmix(o) or len(o) != len(a) + len(b) - n or max(abs(x) for x in o) > 1.0000001:
        return False
    return (all(abs(x - y) < 1e-6 for x, y in zip(o[:len(a) - n], a[:len(a) - n]))
            and all(abs(x - y) < 1e-6 for x, y in zip(o[len(a):], b[n:])))


def _dj_big(o, sc, p):
    return _okmix(o) and max(len(sc["a"]), len(sc["b"])) <= len(o) <= len(sc["a"]) + len(sc["b"]) and \
        (not o or max(abs(x) for x in o) <= 1.0000001)


def _dj_seqtempo(o, sc, p):
    L = len(sc["library"])
    bpm = {t["id"]: t["bpm"] for t in sc["library"]}
    if not isinstance(o, list) or len(o) != sc["n"] or any(x not in bpm for x in o):
        return False
    steps = sorted(abs(bpm[x] - bpm[y]) for x, y in zip(o, o[1:]))
    allp = sorted(abs(a - b) for a in bpm.values() for b in bpm.values() if a != b)
    return steps[len(steps) // 2] <= 0.6 * allp[len(allp) // 2]


def _dj_tempo2(o, sc, p):
    bpm = {t["id"]: t["bpm"] for t in sc["library"]}
    return not _err(o) and o in bpm and o not in sc["history"] and abs(bpm[o] - bpm[sc["history"][-1]]) <= 10


def _dj_blendb(o, sc, p):
    a, b, n = sc["a"], sc["b"], sc["overlap"]
    if not _okmix(o) or len(o) != len(a) + len(b) - n:
        return False
    return max(abs(x) for x in o[len(a) - n:len(a)]) > 0.05


def _dj_combo(o, sc, p):
    return _dj_clip(o, sc, p) and _dj_keepvol(o, sc, p)


def build_dj(rng, tier):
    nn = rng.choice([("next_track", "join_tracks"), ("pick_next", "blend"), ("choose_next", "mix_tracks")])
    fnx, fmx = nn
    L = rng.randint(4, 6)
    letters = "abcdefghjkmnpqrstuvwxyz"
    ids = ["".join(rng.choice(letters) for _ in range(3)) for _ in range(L)]
    while len(set(ids)) < L:
        ids = ["".join(rng.choice(letters) for _ in range(3)) for _ in range(L)]
    bpms = rng.sample(range(70, 176, 7), L)
    lib = [{"id": i, "bpm": b} for i, b in zip(ids, bpms)]
    S = {}

    def add(i, sc, w, kind, why):
        S[i] = {"scen": sc, "w": w, "kind": kind, "why": why}
    ov = rng.choice([40, 50, 60])
    add("x1", {"op": "next", "history": [], "library": lib}, .25, "x", "plays a song from the library")
    add("x2", {"op": "mix", "a": [0.3] * 300, "b": [0.2] * 400, "overlap": ov}, .25, "x", "both songs are heard in the overlap")
    add("x3", {"op": "mix", "a": [0.1 * ((i % 7) - 3) for i in range(80)], "b": [0.05 * ((i % 5) - 2) for i in range(60)],
               "overlap": 0}, .25, "x", "no overlap means the songs are simply joined")
    cases = []
    for _ in range(4):
        h = [rng.choice(ids) for _ in range(rng.randint(1, 5))]
        cases.append(h)
    add("norepeat", {"op": "multi", "library": lib, "cases": cases}, 1, "i",
        "the song that just played does not play again straight away")
    add("round", {"op": "seq", "library": lib, "n": 3 * L}, 1, "i", "every song plays before any song repeats")
    one = lib[:1]
    add("single", {"op": "multi", "library": one, "cases": [[], [one[0]["id"]], [one[0]["id"]] * 3]}, 1, "a",
        "with only one song, keep playing it rather than stopping")
    add("allplayed", {"op": "multi", "library": lib, "cases": [list(ids), ids[::-1] + ids, ids * 2]}, 1, "a",
        "when every song has played, start over instead of going silent")
    b0 = rng.randint(100, 135)
    x = "".join(rng.choice(letters) for _ in range(3))
    near = "nea"
    far = [("fa" + c, b0 + sg * rng.randint(28, 45)) for c, sg in zip("bcd", (1, -1, 1))]
    tl = [{"id": x, "bpm": b0}, {"id": near, "bpm": b0 + rng.choice([-5, -4, 4, 5])}] + [{"id": i, "bpm": b} for i, b in far]
    rng.shuffle(tl)
    add("tempo", {"op": "next", "history": [x], "library": tl}, 1, "i",
        "a DJ moves to a song with a similar tempo; jumping from 100 to 170 bpm jars")
    add("unknown", {"op": "next", "history": ["gone-1", "gone-2", ids[0]], "library": lib}, 1, "i",
        "a song deleted from the library must not break the player")
    add("empty", {"op": "next", "history": [], "library": []}, 1, "i", "an empty library plays nothing (no crash)")
    add("clip", {"op": "mix", "a": [0.0] * 100 + [0.9] * 100, "b": [0.9] * 100 + [0.0] * 100, "overlap": 100}, 1, "i",
        "two loud songs together must not distort (no clipping)")
    add("fade", {"op": "mix", "a": [0.6] * 200, "b": [-0.6] * 200, "overlap": 100}, 1, "i",
        "a transition fades the old song out while the new one comes in")
    rs = random.Random(rng.random())
    add("keepvol", {"op": "mix", "a": [rs.choice([-1, 1]) * rs.uniform(.75, .95) for _ in range(400)], "b": [rs.choice([-1, 1]) * rs.uniform(.75, .95) for _ in range(400)],
                    "overlap": 100}, 1, "a", "loud songs: no distortion, and the parts that are not mixed keep their original volume")
    add("big", {"op": "mix", "a": [0.5] * 100, "b": [0.5] * 300, "overlap": 500}, 1, "i",
        "a transition cannot be longer than a song")
    # scrub pass-1 coverage (2026-11-15), drawn from nothing (the main draws never move): a blend of a silent song a into
    # a song b must let b be heard in the overlap (the brief: the overlap of a and b "play at the same time")
    add("blendb", {"op": "mix", "a": [0.0] * 150, "b": [0.5] * 200, "overlap": ov}, 1, "i",
        "in the overlap both songs play: a blend that drops the incoming song is not a blend")
    # tier 3 extras
    L3 = 6
    b3 = sorted(rng.sample(range(70, 176, 5), L3))
    ids3 = ["t" + str(i) for i in range(L3)]
    lib3 = [{"id": i, "bpm": b} for i, b in zip(ids3, b3)]
    rng.shuffle(lib3)
    add("seqtempo", {"op": "seq", "library": lib3, "n": 3 * L3 + 1}, 1.5, "i3",
        "over a whole set the tempo moves gently, not randomly, while still not repeating")
    n1, n2 = "nx1", "nx2"
    xb = rng.randint(100, 130)
    tl2 = [{"id": "st", "bpm": xb}, {"id": n1, "bpm": xb + 4}, {"id": n2, "bpm": xb + 9}] + \
          [{"id": "ff" + str(i), "bpm": xb + 30 + 9 * i} for i in range(3)]
    rng.shuffle(tl2)
    add("tempo2", {"op": "next", "history": ["st", n1], "library": tl2}, 1.5, "i3",
        "similar tempo is wanted, but not by replaying a song that already played this round")
    ra = random.Random(rng.random())
    add("combo", {"op": "mix", "a": [ra.choice([-1.0, 1.0]) * ra.uniform(.7, 1) for _ in range(300)],
                  "b": [ra.choice([-1.0, 1.0]) * ra.uniform(.7, 1) for _ in range(250)], "overlap": 120}, 1.5, "i3",
        "full-scale songs: no distortion AND the untouched parts stay untouched")
    text = ("Build the playlist brain of a DJ app.\n\n"
            f"    def {fnx}(history, library) -> str\n    def {fmx}(a, b, overlap) -> list\n\n"
            f"- {fnx}: history = ids already played (oldest first, may be empty); library = [{{\"id\": str, \"bpm\": int}}]. "
            "Return the id of the song to play next.\n"
            f"- {fmx}: a and b are lists of audio samples (floats between -1.0 and 1.0). The last `overlap` samples of a "
            "play at the same time as the first `overlap` samples of b. Return the joined list of samples.")
    if tier == 3:
        text += "\n- Used live at parties for hours: many calls in a row, quiet and very loud songs, big and small libraries."
    judges = {"x1": _dj_x1, "x2": _dj_x2, "x3": _dj_x3, "norepeat": _dj_norepeat, "round": _dj_round, "single": _dj_single,
              "allplayed": _dj_allplayed, "tempo": _dj_tempo, "unknown": _dj_unknown, "empty": _dj_empty, "clip": _dj_clip,
              "fade": _dj_fade, "keepvol": _dj_keepvol, "big": _dj_big, "seqtempo": _dj_seqtempo, "tempo2": _dj_tempo2,
              "combo": _dj_combo, "blendb": _dj_blendb}
    return {"text": text, "fn": {"next": fnx, "mix": fmx}, "S": S,
            "t1_pool": ["norepeat", "round", "allplayed", "tempo", "unknown", "clip", "fade", "keepvol", "big"],
            "t3_extra": ["tempo2", "combo"], "params": {}, "judges": judges, "EXTRA": ["blendb"]}


# =========================================================== MONITOR
def _build_ticks(rng, segs, cry, bat0=80, t0=None):
    """segs: (kind, n[, battery]) kinds: q quiet, c cry, s spike, l lost. -> readings, bounds[(kind,start,end)]"""
    t = rng.randint(0, 5000) if t0 is None else t0
    rd, bounds, bat = [], [], bat0
    for sg in segs:
        kind, n = sg[0], sg[1]
        if len(sg) > 2:
            bat = sg[2]
        s = len(rd)
        for _ in range(n):
            if kind == "q":
                snd = round(rng.uniform(0.02, 0.35) * cry, 3)
            elif kind in "cs":
                snd = round(min(1.0, cry + rng.uniform(0.02, 0.3)), 3)
            else:
                snd = None
            rd.append({"t": t, "sound": snd, "battery": None if kind == "l" else bat})
            t += 1
        bounds.append((kind, s, len(rd)))
    return rd, bounds


def _cnt(o, name, a=0, b=None):
    return sum(name in x for x in o[a:b])


def _first(o, name, a=0, b=None):
    for i in range(a, len(o) if b is None else b):
        if name in o[i]:
            return i
    return None


def _okmon(o, n):
    return isinstance(o, list) and len(o) == n


def build_monitor(rng, tier):
    fn = rng.choice(["step", "tick", "on_reading"])
    cry = rng.choice([0.5, 0.6, 0.7, 0.75])
    bth = rng.choice([10, 15, 20])
    low = bth - 5
    S = {}

    def add(i, segs, w, kind, why, bat0=80, **meta):
        rd, bd = _build_ticks(rng, segs, cry, bat0)
        S[i] = {"scen": {"readings": rd}, "w": w, "kind": kind, "why": why, "bounds": bd, **meta}
    add("x1", [("q", 30), ("c", 40), ("q", 30)], .25, "x", "a crying baby raises an alarm")
    add("x2", [("q", 40), ("q", 40, low)], .25, "x", "a low battery raises a warning")
    add("x3", [("q", 200)], .25, "x", "a quiet night raises nothing")
    add("x4", [("q", 20), ("l", 200)], .25, "x", "a dead sensor is reported")
    add("debounce", [("q", 30), ("s", 1), ("q", 30)], 1, "i", "one noisy reading is not a crying baby")
    add("prompt", [("q", 15), ("s", 1), ("q", 10), ("c", 12)], 1, "a", "a real, sustained cry is reported within seconds (a lone blip before it is not)")
    add("storm", [("q", 10), ("c", 300)], 1, "a", "keep reminding while it continues, but do not buzz every second")
    add("episode", [("q", 20), ("c", 15), ("q", 30), ("s", 1), ("q", 30), ("c", 15)], 1, "a", "a second, separate cry is a new alarm (a lone blip between is not)")
    add("blip", [("q", 30), ("l", 1), ("q", 30)], 1, "i", "one dropped reading is not a dead sensor")
    add("lostby", [("q", 30), ("l", 90)], 1, "a", "a dead sensor must be reported within about a minute")
    add("loststart", [("l", 90)], 1, "i", "a sensor that never worked is also reported")
    add("lostagain", [("l", 90), ("q", 30), ("l", 1), ("q", 30), ("l", 90)], 1, "a", "a second outage is a new alarm (a one-second dropout between is not)")
    add("batstorm", [("q", 300, low)], 1, "i", "warn about the battery, but not every second")
    add("notswallowed", [("c", 60, low)], 1, "a", "a cry must not hide the low-battery warning (or the reverse)")
    add("batagain", [("q", 60, low), ("q", 30, 90), ("q", 60, low)], 1, "a", "after recharging, a new low battery warns again")
    add("crylost", [("q", 10), ("c", 20), ("l", 90)], 1, "i3", "the cry and then the failure are both reported")
    night = [("q", 100, 70), ("s", 1), ("q", 100), ("c", 20), ("q", 120), ("l", 100), ("q", 60), ("l", 1), ("q", 60),
             ("c", 200, low), ("q", 100)]
    add("night", night, 2, "i3", "a whole night: no false alarms, every real event reported, no flood")
    text = (f"Baby monitor alarm logic, called once per second.\n\n    def {fn}(state, reading) -> (state, alerts)\n\n"
            "- state is None on the first call; return the new state, it comes back to you next call.\n"
            "- reading = {\"t\": seconds (counts up by 1), \"sound\": 0.0-1.0 loudness or None, \"battery\": percent or None} "
            "(None = the sensor sent nothing this second).\n"
            f"- alerts is a list of strings, any of \"cry\", \"low_battery\", \"sensor_lost\": \"cry\" when sound is at or above "
            f"{cry}; \"low_battery\" when battery is below {bth}; \"sensor_lost\" when the sensor is not sending.")
    if tier == 3:
        text += "\n- It runs all night (thousands of calls) and every alert goes to a parent's phone."

    def seg(i, kind, k=0):
        return [b for b in S[i]["bounds"] if b[0] == kind][k]

    def j(i):
        def f(o, sc, p, i=i):
            n = len(sc["readings"])
            if not _okmon(o, n):
                return False
            return JM[i](o, S[i]["bounds"], n)
        return f
    JM = {}
    _, qs, qe = seg("x1", "q", 0)
    cs, ce = seg("x1", "c")[1:]
    JM["x1"] = lambda o, b, n: _first(o, "cry", cs, ce) is not None and _cnt(o, "cry", 0, cs) == 0
    JM["x2"] = lambda o, b, n: _first(o, "low_battery", 40, 80) is not None and _cnt(o, "low_battery", 0, 40) == 0
    JM["x3"] = lambda o, b, n: all(len(x) == 0 for x in o)
    JM["x4"] = lambda o, b, n: _first(o, "sensor_lost", 20, 220) is not None and _cnt(o, "sensor_lost", 0, 20) == 0
    JM["debounce"] = lambda o, b, n: _cnt(o, "cry") == 0
    JM["prompt"] = lambda o, b, n: _cnt(o, "cry", 0, 26) == 0 and _first(o, "cry", 26, 34) is not None
    JM["storm"] = lambda o, b, n: 2 <= _cnt(o, "cry") <= 75
    JM["episode"] = lambda o, b, n: _first(o, "cry", 20, 28) is not None and _cnt(o, "cry", 40, 96) == 0 and _first(o, "cry", 96, 104) is not None
    JM["blip"] = lambda o, b, n: _cnt(o, "sensor_lost") == 0
    JM["lostby"] = lambda o, b, n: _first(o, "sensor_lost", 30, 90) is not None
    JM["loststart"] = lambda o, b, n: _first(o, "sensor_lost", 0, 60) is not None
    JM["lostagain"] = lambda o, b, n: _first(o, "sensor_lost", 0, 60) is not None and _cnt(o, "sensor_lost", 120, 125) == 0 and _first(o, "sensor_lost", 151, 211) is not None
    JM["batstorm"] = lambda o, b, n: 1 <= _cnt(o, "low_battery") <= 75
    JM["notswallowed"] = lambda o, b, n: _first(o, "low_battery", 0, 60) is not None and _first(o, "cry", 0, 60) is not None and _cnt(o, "low_battery") <= 20
    JM["batagain"] = lambda o, b, n: _first(o, "low_battery", 0, 60) is not None and _first(o, "low_battery", 90, 98) is not None and _cnt(o, "low_battery", 90, 150) <= 20
    JM["crylost"] = lambda o, b, n: _first(o, "cry", 10, 30) is not None and _first(o, "sensor_lost", 30, 120) is not None

    def night_j(o, b, n):
        (q1, a1, b1), (s1, a2, b2), (q2, a3, b3), (c1, a4, b4), (q3, a5, b5), (l1, a6, b6), (q4, a7, b7), (l2, a8, b8), \
            (q5, a9, b9), (c2, a10, b10), (q6, a11, b11) = b
        return (_cnt(o, "cry", 0, a4) == 0 and _cnt(o, "sensor_lost", 0, a6) == 0
                and _first(o, "cry", a4, a4 + 8) is not None and _first(o, "sensor_lost", a6, a6 + 60) is not None
                and _cnt(o, "sensor_lost", a8, a8 + 10) == 0 and _first(o, "cry", a10, a10 + 8) is not None
                and _first(o, "low_battery", a10, b10) is not None and sum(len(x) for x in o) <= 150
                and _cnt(o, "low_battery", 0, a10) == 0)
    JM["night"] = night_j
    judges = {i: j(i) for i in S}
    return {"text": text, "fn": {"main": fn}, "S": S,
            "t1_pool": ["debounce", "prompt", "storm", "episode", "blip", "lostby", "loststart", "lostagain", "batstorm",
                        "notswallowed", "batagain"],
            "t3_extra": ["crylost", "night"], "params": {"cry": cry, "bat": bth}, "judges": judges}


# =========================================================== common
BUILD = {"sched": build_sched, "dj": build_dj, "monitor": build_monitor, **_M.BUILD, **_T.BUILD}
ANTI = {"evening", "single", "allplayed", "keepvol", "prompt", "storm", "episode", "lostby", "lostagain",
        "notswallowed", "batagain", "batstorm"}
LIT_FAIL = {"sched": {"hours", "weekend", "eod"},
            "dj": {"norepeat", "round", "tempo", "clip", "fade", "big", "unknown"},
            "monitor": {"debounce", "blip", "batstorm"}}
FORCE = {"sched": ["evening"], "dj": ["allplayed", "keepvol"], "monitor": ["prompt", "episode", "storm"]}
KILL = {"sched": ["weekendreq"], "dj": [],
        "monitor": ["batagain", "lostagain", "notswallowed"]}
# failed by every model in the 12-seed probe and not an owner-named example: kept as headroom at half weight
DOWN = {("backup", "lastgood"), ("backup", "onlycopy"), ("pay", "backwards"),
        ("monitor", "blip"), ("notify", "nosevquiet"), ("notify", "quiet"), ("notify", "critcap")}
FORCE_W = 2.0
#: per-domain override of the pad-killer weight (empty since split.discount came back, F-055 panel)
FORCE_WD = {}
#: level-3 pad-killer weights: thermo carries frost again (F-055), which the blanket-safeguard pad passes, so its two anti
#: traps weigh 2 at level 3 to keep pad under 0.6
FORCE_W3 = {("thermo", "window"): 2.0, ("thermo", "warmhalf"): 2.0}
#: grading-only widening (F-055 panel part 2, 2026-11-14): checks where all three blind readers call "raise ValueError" a sound
#: answer next to the judge's own one ("merge, or raise; never double-pay"; "raise or clamp at 0"; "clamp or raise"). A ValueError
#: now passes; every answer that passed before still passes. No prompt changes.
#: Asked and NOT accepted (second question, 2 of 3 "sound" needed): thermo empty (raise on an empty schedule: 0/3 sound),
#: returns overreturn (0/3: one bad slip must not cost the whole total; dropping that slip still passes), returns sameid
#: (0/3: an identical copy is one slip). Each check is ruled on its own; there is no blanket "any ValueError passes".
ERR_ACCEPT_PANEL = {("pay", "dupe"), ("pay", "overlap"), ("pay", "otdupe"), ("pay", "bigbreak"), ("dj", "big"),
                    ("grades", "zeromax"), ("split", "discount"), ("split", "capvoucher")}
#: contested checks (lead 2026-11-14): kept in place and reported, weight 0 (out of numerator and denominator) until the
#: scenario is repaired post-M1; see panel2/tiers.json
CONTESTED_W0 = {("backup", "nospace"), ("backup", "pinnedspace"), ("monitor", "debounce"), ("monitor", "storm", 1)}
#: compensation, grading-only: with nospace at 0 the backup pad rose to 0.65 (> 0.6); the panel-kept careful checks the pad fails
#: (lastgood, onlycopy: 3 of 3 readers) leave DOWN and weigh in full again
CONTESTED_COMP = {("backup", "lastgood"), ("backup", "onlycopy")}
#: one rule for refusals (scrub, 2026-11-14): a repeated id may be refused with a ValueError like any other bad input. The
#: family already accepts that refusal for a duplicate shift (pay dupe / otdupe, panel 3/3) and for bad values (returns
#: zeroqty, mileage nonpos, rsvp badguests); sameid alone failed it (returns 0/3 in the F-055 second question), which cost
#: careful programs that noticed the duplicate and paid nothing twice. A refusal of different ids (twin, splitslips) still fails.
ERR_ACCEPT_DUP = {("returns", "sameid"), ("ledger", "sameid"), ("expenses", "sameid")}
#: checks whose judge reads a raised error itself (raising ValueError is one of the accepted answers)
ERR_JUDGED = {("pay", "backwards"), ("mileage", "nonpos"), ("rsvp", "badguests"), ("returns", "unknown"),
              ("returns", "zeroqty")} | ERR_ACCEPT_PANEL | ERR_ACCEPT_DUP
WRAP = ("Write a Python 3 module (standard library only) for the brief below. Reply with ONE ```python code block containing "
        "the whole module. Real people will use it, so decide sensible behaviour yourself; nobody is available to answer questions.\n\n")


def pick_domain(seed, tier=1):
    """uniform over the bank: every block of len(bank) consecutive seeds is one shuffled pass over all domains"""
    bank = DOMS if tier == 1 else DOMS_ALL
    blk, k = divmod(seed - 1, len(bank))
    order = list(bank)
    random.Random(blk * 7 + 11).shuffle(order)
    return order[k]


#: base-bank domains with extra stated-rule variants at tier 1 (v = 0 is the original brief, byte for byte; the others add or
#: change a rule in the brief). The variant comes from the seed (its own stream, so the main draws never move) unless pinned;
#: `ffseed` pins v = 0 so the fast-fail instances reproduce.
VARIANT_DOMS = ("meds", "thermo", "split", "backup", "pay")
N_VARIANTS = 3


def _variant_of(seed, tier, dom, variant):
    if tier != 1 or dom not in VARIANT_DOMS:
        return 0
    return int(variant) if variant is not None else random.Random(seed * 6151 + 7).randrange(N_VARIANTS)


#: level-5 briefs (three interacting functions each): domain id -> module with make(seed, wrap) / judges(seed)
_L5 = {"meds3": _I3, "car3": _CAR, "lib3": _LIB, "wh3": _WH}
DOMS5 = list(_L5)


def pick5(seed):
    """uniform over the level-5 briefs: every block of len(DOMS5) consecutive seeds is one shuffled pass over all of them"""
    blk, k = divmod(seed - 1, len(DOMS5))
    order = list(DOMS5)
    random.Random(blk * 13 + 5).shuffle(order)
    return order[k]


def make_instance(seed, tier, dom=None, variant=None):
    if tier == 5:      # level 5: one of the three-function briefs (DOMS5); a `dom` outside the list is ignored
        return _L5[dom if dom in _L5 else pick5(seed)].make(seed, WRAP)
    rng = random.Random(seed * 104729 + tier * 7919)
    dom = dom or pick_domain(seed, tier)
    v = _variant_of(seed, tier, dom, variant)
    b = BUILD[dom](rng, tier, v) if dom in VARIANT_DOMS else BUILD[dom](rng, tier)
    S = b["S"]
    xs = [i for i in S if S[i]["kind"] == "x"]
    if dom in NEW:
        pool = list(b["POOL"])
        rng.shuffle(pool)
        imp = list(b["FORCE"]) + pool[:rng.choice([5, 6]) - len(b["FORCE"])] if tier == 1 else \
            list(b["FORCE"]) + list(b["POOL"])
    elif tier == 1:
        lf = [i for i in b["t1_pool"] if i in LIT_FAIL[dom]]
        lp = list(KILL[dom])
        rng.shuffle(lf); rng.shuffle(lp)
        imp = lf[:6 - len(FORCE[dom]) - 1] + FORCE[dom] + lp[:1]
    else:
        imp = list(b["t1_pool"]) + list(b["t3_extra"])
    # scrub pass-1 coverage (2026-11-15): checks every level-1 instance carries after the drawn ones (stated rules a program
    # could break while passing every drawn check); appended last and drawn from nothing, so the selection never moves
    extra = [i for i in b.get("EXTRA", ()) if i not in imp and i not in xs] if tier == 1 else []
    imp = imp + extra
    ids = xs + imp
    if tier == 3:
        for i in ids:
            if S[i]["kind"] == "a":
                S[i]["w"] *= 1.5
    for i in ids:
        if (dom, i) in CONTESTED_W0 or (dom, i, tier) in CONTESTED_W0:
            S[i]["w"] = 0.0
        elif (dom, i) in CONTESTED_COMP:
            pass                                          # full weight: carries the pad floor the contested check carried
        elif (dom, i) in DOWN:
            S[i]["w"] = 0.5
        elif tier == 3 and (dom, i) in FORCE_W3:
            S[i]["w"] = FORCE_W3[(dom, i)]
        elif dom in FORCE_WD and i in b["FORCE"]:
            S[i]["w"] = FORCE_WD[dom]
        elif dom in NEW and tier == 1 and i in b["FORCE"]:
            S[i]["w"] = FORCE_WD.get(dom, FORCE_W)      # the pad-killers: every strong model passes them, they anchor the generic-safeguard floor
    exps = [{"id": i, "w": S[i]["w"], "kind": S[i]["kind"], "why": S[i]["why"], "scen": S[i]["scen"]} for i in ids]
    public = {"seed": seed, "tier": tier, "domain": dom, "fn": b["fn"],
              "params": ({**b["params"], "v": v} if dom in VARIANT_DOMS else b["params"]), "n_exp": len(imp)}
    hidden = {"exps": exps}
    return {"prompt": WRAP + b["text"], "public": public, "hidden": hidden}


def _judges(inst):
    if inst["public"]["tier"] == 5:
        return _L5[inst["public"]["domain"]].judges(inst["public"]["seed"])
    dom = inst["public"]["domain"]
    pub = inst["public"]
    rng = random.Random(pub["seed"] * 104729 + pub["tier"] * 7919)
    # rebuild judges (closures over the same draws) deterministically, exactly as gen() did
    if dom in VARIANT_DOMS:
        return BUILD[dom](rng, pub["tier"], pub["params"].get("v", 0))["judges"]
    return BUILD[dom](rng, pub["tier"])["judges"]


# =========================================================== reference / baseline / pad / null solutions
SCHED_REF = '''
def @FN@(requests, existing, now, tz):
    DAY = 1440; OPEN = 540; CLOSE = 1020; BUF = 10
    busy = [(e["start"], e["end"]) for e in existing]
    out = []
    def conflict(s, e, buf):
        return [be + buf for bs, be in busy if s < be + buf and bs - buf < e]
    def find(dur):
        t = max(now, 0)
        for _ in range(20000):
            e = t + dur
            ls = t + tz; day = ls // DAY
            if day % 7 >= 5:
                t = (day + 1) * DAY - tz; continue
            if ls % DAY < OPEN:
                t = day * DAY + OPEN - tz; continue
            if e + tz - day * DAY > CLOSE:
                t = (day + 1) * DAY - tz; continue
            hit = conflict(t, e, BUF)
            if hit:
                t = min(hit); continue
            return t
        return None
    for r in requests:
        s = r.get("start"); dur = r["duration"]
        if s is not None and s >= now and not conflict(s, s + dur, 0):
            pass
        else:
            s = find(dur)
        if s is None:
            continue
        busy.append((s, s + dur)); out.append({"id": r["id"], "start": s})
    return out
'''

SCHED_LIT = '''
def @FN@(requests, existing, now, tz):
    busy = [(e["start"], e["end"]) for e in existing]
    out = []
    def conflict(s, e):
        return [be for bs, be in busy if s < be and bs < e]
    for r in requests:
        s = r.get("start"); dur = r["duration"]
        if s is None or conflict(s, s + dur):
            s = now
            while True:
                h = conflict(s, s + dur)
                if not h: break
                s = min(h)
        busy.append((s, s + dur)); out.append({"id": r["id"], "start": s})
    return out
'''

SCHED_PAD = '''
def @FN@(requests, existing, now, tz):
    DAY = 1440; OPEN = 540; CLOSE = 1020; BUF = 15
    busy = [(e["start"], e["end"]) for e in existing]
    per = {}
    out = []
    def conflict(s, e):
        return [be + BUF for bs, be in busy if s < be + BUF and bs - BUF < e]
    for r in requests:
        dur = r["duration"]
        t = max(now, 0)
        for _ in range(20000):
            e = t + dur
            ls = t + tz; day = ls // DAY
            if day % 7 >= 5 or per.get(day, 0) >= 4:
                t = (day + 1) * DAY - tz; continue
            if ls % DAY < OPEN:
                t = day * DAY + OPEN - tz; continue
            if e + tz - day * DAY > CLOSE:
                t = (day + 1) * DAY - tz; continue
            if (ls % DAY) < 780 and (e + tz - day * DAY) > 720:
                t = day * DAY + 780 - tz; continue
            hit = conflict(t, e)
            if hit:
                t = min(hit); continue
            break
        else:
            continue
        per[(t + tz) // DAY] = per.get((t + tz) // DAY, 0) + 1
        busy.append((t, t + dur)); out.append({"id": r["id"], "start": t})
    return out
'''

SCHED_NULL = '''
def @FN@(requests, existing, now, tz):
    return []
'''

DJ_REF = '''
def @NEXT@(history, library):
    if not library:
        return None
    ids = [t["id"] for t in library]
    if len(ids) == 1:
        return ids[0]
    bpm = {t["id"]: t["bpm"] for t in library}
    last = history[-1] if history and history[-1] in bpm else None
    played = set()
    for h in history:
        if h in bpm:
            if len(played) >= len(ids) or h in played:
                played = set()
            played.add(h)
    if len(played) >= len(ids):
        played = set()
    cand = [i for i in ids if i not in played]
    if last in cand and len(cand) > 1:
        cand.remove(last)
    if last is None:
        return cand[0]
    return min(cand, key=lambda i: (abs(bpm[i] - bpm[last]), ids.index(i)))

def @MIX@(a, b, overlap):
    n = max(0, min(int(overlap), len(a), len(b)))
    out = list(a[:len(a) - n])
    for i in range(n):
        wb = (i + 1) / (n + 1)
        v = a[len(a) - n + i] * (1 - wb) + b[i] * wb
        out.append(max(-1.0, min(1.0, v)))
    return out + list(b[n:])
'''

DJ_LIT = '''
import random
def @NEXT@(history, library):
    return random.Random(len(history) * 7 + 3).choice(library)["id"]

def @MIX@(a, b, overlap):
    n = overlap
    return list(a[:len(a) - n]) + [x + y for x, y in zip(a[len(a) - n:], b[:n])] + list(b[n:])
'''

DJ_PAD = '''
def @NEXT@(history, library):
    ids = [t["id"] for t in library]
    bpm = {t["id"]: t["bpm"] for t in library}
    cand = [i for i in ids if i not in history]
    if not cand:
        return None
    last = next((h for h in reversed(history) if h in bpm), None)
    if last is None:
        return cand[0]
    return min(cand, key=lambda i: abs(bpm[i] - bpm[last]))

def @MIX@(a, b, overlap):
    n = max(0, min(int(overlap), len(a), len(b)))
    out = list(a[:len(a) - n])
    for i in range(n):
        wb = (i + 1) / (n + 1)
        out.append(a[len(a) - n + i] * (1 - wb) + b[i] * wb)
    out += list(b[n:])
    pk = max([abs(x) for x in out] + [1e-9])
    return [max(-1.0, min(1.0, x * 0.95 / pk)) for x in out]
'''

DJ_NULL = '''
def @NEXT@(history, library):
    return library[0]["id"] if library else None
def @MIX@(a, b, overlap):
    return list(a) + list(b)
'''

MON_REF = '''
CRY = @CRY@
BAT = @BAT@
def @FN@(state, reading):
    s = state or {"above": 0, "below": 0, "in_cry": False, "last_cry": -1e9, "lost": 0, "in_lost": False,
                  "last_lost": -1e9, "in_bat": False, "last_bat": -1e9}
    t = reading["t"]; snd = reading.get("sound"); bat = reading.get("battery")
    al = []
    if snd is not None:
        if snd >= CRY:
            s["above"] += 1; s["below"] = 0
        else:
            s["below"] += 1; s["above"] = 0
            if s["below"] >= 5:
                s["in_cry"] = False
        if s["above"] >= 3 and (not s["in_cry"] or t - s["last_cry"] >= 30):
            al.append("cry"); s["in_cry"] = True; s["last_cry"] = t
    if bat is not None:
        if bat < BAT:
            if not s["in_bat"] or t - s["last_bat"] >= 60:
                al.append("low_battery"); s["in_bat"] = True; s["last_bat"] = t
        elif bat >= BAT + 5:
            s["in_bat"] = False
    if snd is None and bat is None:
        s["lost"] += 1; s["above"] = 0
        if s["lost"] >= 15 and (not s["in_lost"] or t - s["last_lost"] >= 60):
            al.append("sensor_lost"); s["in_lost"] = True; s["last_lost"] = t
    else:
        s["lost"] = 0; s["in_lost"] = False
    return s, al
'''

MON_LIT = '''
CRY = @CRY@
BAT = @BAT@
def @FN@(state, reading):
    al = []
    snd = reading.get("sound"); bat = reading.get("battery")
    if snd is not None and snd >= CRY: al.append("cry")
    if bat is not None and bat < BAT: al.append("low_battery")
    if snd is None and bat is None: al.append("sensor_lost")
    return state, al
'''

MON_PAD = '''
CRY = @CRY@
BAT = @BAT@
def @FN@(state, reading):
    s = state or {"c": {}, "last": {}}
    t = reading["t"]; snd = reading.get("sound"); bat = reading.get("battery")
    cond = {"cry": snd is not None and snd >= CRY, "sensor_lost": snd is None and bat is None,
            "low_battery": bat is not None and bat < BAT}
    al = []
    for k in ("cry", "sensor_lost", "low_battery"):
        s["c"][k] = s["c"].get(k, 0) + 1 if cond[k] else 0
        if s["c"][k] >= 30 and t - s["last"].get(k, -1e9) >= 600 and not al:
            al.append(k); s["last"][k] = t
    return s, al
'''

MON_NULL = '''
def @FN@(state, reading):
    return state, []
'''


def _sub(src, inst):
    f = inst["public"]["fn"]
    p = inst["public"]["params"]
    s = src.replace("@FN@", f.get("main", "")).replace("@NEXT@", f.get("next", "")).replace("@MIX@", f.get("mix", ""))
    return s.replace("@CRY@", str(p.get("cry", ""))).replace("@BAT@", str(p.get("bat", ""))).replace("@PARAMS@", repr(p)).replace("@V@", str(p.get("v", 0)))


_SRC = {**{k: v[:4] for k, v in {**_M.SRC, **_T.SRC}.items()}, "sched": (SCHED_REF, SCHED_LIT, SCHED_PAD, SCHED_NULL), "dj": (DJ_REF, DJ_LIT, DJ_PAD, DJ_NULL),
        "monitor": (MON_REF, MON_LIT, MON_PAD, MON_NULL),
        "meds3": (_I3.REF, _I3.LIT, _I3.PAD, _I3.NULL), "car3": (_CAR.REF, _CAR.LIT, _CAR.PAD, _CAR.NULL),
        "lib3": (_LIB.REF, _LIB.LIT, _LIB.PAD, _LIB.NULL), "wh3": (_WH.REF, _WH.LIT, _WH.PAD, _WH.NULL)}


def reference_code(inst):
    return _sub(_SRC[inst["public"]["domain"]][0], inst)


def _literal(inst):
    """the literal-brief solution (anchors the 30 points together with null())"""
    return _sub(_SRC[inst["public"]["domain"]][1], inst)


def alt(inst):
    """a deliberately different but reasonable realisation (new domains only), for hand-checking the grader"""
    return _sub({**_M.SRC, **_T.SRC}[inst["public"]["domain"]][4], inst)


def baseline(inst):
    """best trivial strategy: the better (by this grader) of the literal-brief solution and the do-nothing one"""
    c = [_sub(_SRC[inst["public"]["domain"]][i], inst) for i in (1, 3)]
    return max(c, key=lambda x: check(inst, x)["q"])


def pad(inst):
    return _sub(_SRC[inst["public"]["domain"]][2], inst)


def null(inst):
    return _sub(_SRC[inst["public"]["domain"]][3], inst)


# =========================================================== CrucibleForge adapter
# Everything above is the fast-fail question IMPLIED as ported (ff/q/implied.py at the commit named in the
# header; the domain bank part 2 lives in _implied_more.py). Below: the engine contract.
import base64
import hashlib
import functools
import re as _re
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import gen as _gen

FAMILY = "implied"
CPU_S = 20.0
MAX_TOKENS = 65536
#: tier 1 = a pocket brief (5-6 implied expectations drawn from the domain's pool);
#: tier 3 = the whole pool and the explicit rungs weigh 1.5.  answer_tokens = median reference-answer tokens
#: (chars/4), re-measured in tests/test_gen_implied.py.
LEVELS = {
    1: {"tier": 1, "answer_tokens": 159},
    3: {"tier": 3, "answer_tokens": 199},
    5: {"tier": 5, "answer_tokens": 400},   # three-function briefs (_implied3.py meds3, _implied5_{car,lib,wh}.py)
}
VARIANTS = ()

#: points ladder of the fast-fail harness (ff/core.py): invalid 0, the best trivial solution 30, the reference 100,
#: linear between, a valid answer below the trivial one floors at 10. score = points / 100.
POINTS_BASE, POINTS_REF, POINTS_FLOOR = 30.0, 100.0, 10.0

#: pin of _implied_more.py: chains.revision() digests only THIS file, so a change to the sibling must change this
#: line (tests/test_gen_implied.py::test_sibling_pin fails until it does)
_MORE_SHA = "585360ccca1b422d"   # FIXNOW 17 pay backwards (2026-11-15)
#: pin of _implied_t1.py (the everyday tier-1 domains), same reason
_T1_SHA = "bd43a7a15c2509b2"
#: pin of _implied3.py (level 5), same reason
_I3_SHA = "d5a109dfffbd8010"   # FIXNOW 21 renag scenario + 22 dup dropped (2026-11-15)
#: pins of the other level-5 briefs (_implied5_car.py / _implied5_lib.py / _implied5_wh.py), same reason
_CAR_SHA = "fadcd995bc454592"
_LIB_SHA = "e3b373232faa2293"
_WH_SHA = "e31147ff3c9457f6"

# the in-sandbox driver: the model's module is exec'd as `solver`, each scenario runs under an alarm, results are
# serialised one by one (an unserialisable result fails its own scenario only)
_PRO = r"""
import base64, json, random, signal, sys, types, os, hashlib
# one reply, one grade (as planted_opt 925d006): fixed draws for random, Random() and os.urandom; hash order via _DET_ENV
random.seed(0)
class _DetRandom(random.Random):
    def __init__(self, x=None):
        super().__init__(0 if x is None else x)
random.Random = _DetRandom
_uc = [0]
def _urandom(n):
    out = b""
    while len(out) < n:
        _uc[0] += 1; out += hashlib.sha256(b"bench-implied-%d" % _uc[0]).digest()
    return out[:n]
os.urandom = _urandom
d = json.loads(base64.b64decode("@BLOB@").decode("utf-8"))
signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(BaseException("alarm")))
solver = types.ModuleType("solver")
sys.modules["solver"] = solver
try:
    signal.alarm(8)
    exec(compile(d["code"], "solver.py", "exec"), solver.__dict__)
    signal.alarm(0)
    for _v in d["fn"].values():
        getattr(solver, _v)
except BaseException as _e:
    signal.alarm(0)
    sys.stdout.write("\nBENCH_IMPLIED_ERR:" + (type(_e).__name__ + ": " + str(_e))[:200].replace("\n", " ") + "\n")
    sys.exit(0)
"""
_EPI = r"""
_res = {}
for _k, _v in out.items():
    try:
        json.dumps(_v)
        _res[_k] = _v
    except BaseException:
        _res[_k] = {"__err": "unserialisable result"}
sys.stdout.write("\nBENCH_IMPLIED:" + json.dumps(_res) + "\n")
"""


def _harness_body() -> str:
    """The ff driver, re-aimed: the model's module is exec'd as `solver` in the prologue (no files), the scenarios
    run under the driver's own per-scenario alarm, results are serialised one by one."""
    body = DRIVER
    for old, new in (("import json, signal, solver\n", ""), ("d = json.load(open('in.json'))\n", ""),
                     ("json.dump(out, open('out.json', 'w'))\n", "")):
        assert old in body, old
        body = body.replace(old, new)
    return body


_HARNESS = _PRO + _harness_body() + _EPI


#: the graded run's whole environment (grading-only, 2026-11-14; codebank and PYTRAP pin the same): str hash() and set
#: order (PYTHONHASHSEED), local time (TZ) and locale are fixed, nothing else from the host leaks in.
_DET_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONHASHSEED": "0", "TZ": "UTC", "LC_ALL": "C", "LANG": "C",
            "PYTHONDONTWRITEBYTECODE": "1"}


def _det_cmd(cmd: list) -> list:
    """`python -I` implies -E and so ignores PYTHONHASHSEED; `-s -S -P` keeps what -I isolates here (no user site, no
    site import, no script dir on sys.path; the env is _DET_ENV only, so no other PYTHON* variable can reach it) and
    lets the hash seed apply."""
    cmd = list(cmd)
    i = len(cmd) - 1 - cmd[::-1].index("-I")
    return cmd[:i] + ["-s", "-S", "-P"] + cmd[i + 1:]


def _run(code: str, inst: dict, cpu_s: float = CPU_S) -> dict:
    """Run ``code`` in the engine's bwrap sandbox (no network, private tmpfs, 2 GiB, CPU limit); the scenarios'
    outputs come back as JSON and are judged OUTSIDE it. -> {ok, out, cpu_s, err}"""
    import resource
    from .. import graders as _g
    if not (code or "").strip():
        return {"ok": False, "out": None, "cpu_s": 0.0, "err": "no code in response"}
    _g.require_sandbox()
    pub, exps = inst["public"], inst["hidden"]["exps"]
    blob = base64.b64encode(json.dumps({
        "code": code, "dom": pub["domain"], "fn": pub["fn"],
        "scens": {e["id"]: e["scen"] for e in exps}}).encode("utf-8", "replace")).decode("ascii")
    lim = int(cpu_s) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-implied-") as td:
        path = Path(td) / "harness.py"
        path.write_text(_HARNESS.replace("@BLOB@", blob), encoding="utf-8")
        try:
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            p = subprocess.run(_det_cmd(_g._sandbox_cmd(str(path), td)), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=cpu_s * 4 + 30, cwd=td, env=dict(_DET_ENV),
                               preexec_fn=pre)
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            out, rc, err = p.stdout, p.returncode, p.stderr[-300:]
        except subprocess.TimeoutExpired:
            return {"ok": False, "out": None, "cpu_s": cpu_s * 4, "err": "wall timeout"}
    cpu = (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime)
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("BENCH_IMPLIED")), None)
    if line is None or line.startswith("BENCH_IMPLIED_ERR:"):
        why = line[len("BENCH_IMPLIED_ERR:"):] if line else (err or f"rc={rc}")[-200:]
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": "crash: " + why.strip()[:200]}
    try:
        res = json.loads(line[len("BENCH_IMPLIED:"):])
    except ValueError:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": "unreadable output"}
    if cpu > cpu_s:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": f"over CPU limit ({cpu:.1f}s > {cpu_s:g}s)"}
    return {"ok": True, "out": res, "cpu_s": round(cpu, 2), "err": ""}


#: the panel tier of each checked expectation (F-055: 3 independent Opus readers; 'competent' = any competent engineer
#: handles it, 'careful' = only a careful one, the fair headroom above the reference). Carried on every result and in the
#: grader config so stored rows can be split by tier; scoring does not read it. Checks no panel has rated yet: 'unrated'.
CHECK_TIER = {
    ("backup", "lastgood"): "careful",
    ("backup", "nospace"): "contested",   # strong models keep the 7-day window (explicit rule); see panel2 tiers.json
    ("backup", "onlycopy"): "careful",
    ("backup", "weeklykeeper"): "careful",
    ("car3", "adjacent"): "competent",
    ("car3", "cancel"): "competent",
    ("car3", "cent"): "competent",
    ("car3", "fallback"): "careful",
    ("car3", "fcfs"): "careful",
    ("car3", "multiday"): "competent",
    ("car3", "onecar"): "careful",
    ("car3", "onecarex"): "careful",
    ("car3", "past"): "careful",
    ("car3", "seats"): "careful",
    ("car3", "sharedcap"): "careful",
    ("car3", "tzsplit"): "competent",
    ("invoices", "credit"): "careful",
    ("invoices", "overpaid"): "competent",
    ("invoices", "partial"): "competent",
    ("invoices", "samecust"): "careful",
    ("lib3", "capped"): "competent",
    ("lib3", "group"): "competent",
    ("lib3", "latereturn"): "competent",
    ("lib3", "notdue"): "competent",
    ("lib3", "paid"): "careful",
    ("lib3", "reloan"): "careful",
    ("lib3", "renew"): "competent",
    ("lib3", "tzstmt"): "competent",
    ("lib3", "voided"): "competent",
    ("meds", "night"): "careful",
    ("meds", "tzday"): "competent",
    ("meds", "yday"): "competent",
    ("meds3", "burst"): "competent",
    ("meds3", "critcap"): "careful",
    ("meds3", "critnight"): "careful",
    ("meds3", "dup"): "careful",
    ("meds3", "night"): "careful",
    ("meds3", "nodup"): "careful",
    ("meds3", "plannight"): "careful",
    ("meds3", "renag"): "careful",
    ("meds3", "taken"): "competent",
    ("meds3", "yday"): "competent",
    ("sched", "combo"): "careful",
    ("sched", "eod"): "careful",
    ("sched", "evening"): "competent",
    ("sched", "hours"): "careful",
    ("sched", "latestart"): "competent",
    ("sched", "many"): "careful",
    ("sched", "past"): "competent",
    ("sched", "tzday"): "careful",
    ("sched", "weekend"): "careful",
    ("sched", "weekendreq"): "competent",
    ("split", "discount"): "careful",
    ("split", "nonpayer"): "competent",
    ("split", "spread"): "careful",
    ("split", "tipsum"): "careful",
    ("thermo", "empty"): "careful",
    ("thermo", "frost"): "careful",
    ("thermo", "unsorted"): "competent",
    ("thermo", "warmhalf"): "careful",
    ("thermo", "window"): "careful",
    ("thermo", "wrap"): "careful",
    ("wh3", "allornone"): "careful",
    ("wh3", "bulk"): "careful",
    ("wh3", "fifo"): "careful",
    ("wh3", "multi"): "careful",
    ("wh3", "notyet"): "careful",
    ("wh3", "packedres"): "careful",
    ("wh3", "selfres"): "careful",
    ("wh3", "tzcut"): "competent",
    # panel part 2 (panel2/TABLE-pay-dj.md): every check listed by 3 of 3 readers
    ("pay", "dupe"): "careful", ("pay", "overlap"): "careful", ("pay", "bigbreak"): "careful", ("pay", "otdupe"): "careful",
    ("pay", "backwards"): "competent", ("pay", "odd"): "explicit",
    ("dj", "norepeat"): "competent", ("dj", "round"): "careful", ("dj", "allplayed"): "careful", ("dj", "tempo"): "competent",
    ("dj", "unknown"): "careful", ("dj", "clip"): "careful", ("dj", "fade"): "careful", ("dj", "big"): "competent",
    ("dj", "keepvol"): "careful", ("dj", "tempo2"): "careful", ("dj", "combo"): "careful",
    # panel part 2 (panel2/TABLE-b23.md + second questions); level-3 overrides (monitor) live in panel2/tiers.json
    ("backup", "corruptlone"): "careful",
    ("backup", "pinnedspace"): "contested",
    ("board", "negative"): "careful",
    ("board", "nones"): "competent",
    ("board", "onlynone"): "competent",
    ("board", "tieall"): "explicit",
    ("board", "tieboard"): "explicit",
    ("expenses", "credit"): "careful",
    ("expenses", "overapproved"): "competent",
    ("expenses", "pending"): "careful",
    ("expenses", "sameid"): "careful",
    ("expenses", "twin"): "explicit",
    ("expenses", "twin2"): "explicit",
    ("grades", "twin"): "explicit",
    ("grades", "twin2"): "explicit",
    ("grades", "ungraded"): "competent",
    ("grades", "zeromax"): "careful",
    ("invoices", "graceedge"): "competent",
    ("ledger", "oldfirst"): "competent",
    ("ledger", "reverse"): "careful",
    ("ledger", "reverseredeem"): "careful",
    ("ledger", "sameid"): "careful",
    ("ledger", "twoearns"): "careful",
    ("ledger", "twoearns2"): "careful",
    ("ledger", "unsorted"): "careful",
    ("meds", "graceedge"): "competent",
    ("meds", "nightsort"): "competent",
    ("mileage", "nonpos"): "careful",
    ("mileage", "roundtrip"): "careful",
    ("mileage", "splitday"): "competent",
    ("mileage", "unknown"): "competent",
    ("monitor", "batagain"): "careful",
    ("monitor", "batstorm"): "careful",
    ("monitor", "blip"): "careful",
    ("monitor", "crylost"): "careful",
    ("monitor", "debounce"): "contested",
    ("monitor", "episode"): "careful",
    ("monitor", "lostagain"): "careful",
    ("monitor", "lostby"): "careful",
    ("monitor", "loststart"): "careful",
    ("monitor", "night"): "careful",
    ("monitor", "notswallowed"): "explicit",
    ("monitor", "prompt"): "careful",
    ("monitor", "storm"): "contested",
    ("notify", "critcap"): "careful",
    ("notify", "critdup"): "careful",
    ("notify", "critnight"): "careful",
    ("notify", "dup"): "careful",
    ("notify", "dupold"): "careful",
    ("notify", "nosevquiet"): "careful",
    ("notify", "quiet"): "careful",
    ("notify", "tzday"): "careful",
    ("notify", "tzquiet"): "careful",
    ("returns", "cumulative"): "careful",
    ("returns", "overreturn"): "careful",
    ("returns", "sameid"): "careful",
    ("returns", "splitslips"): "explicit",
    ("returns", "splitslips2"): "explicit",
    ("returns", "unknown"): "competent",
    ("returns", "zeroqty"): "careful",
    ("rsvp", "badguests"): "competent",
    ("rsvp", "changed"): "careful",
    ("rsvp", "dupe"): "careful",
    ("rsvp", "latest"): "careful",
    ("rsvp", "twins"): "explicit",
    ("rsvp", "twins2"): "explicit",
    ("shipping", "alldigital"): "competent",
    ("shipping", "digitalfree"): "competent",
    ("shipping", "digitalmix"): "competent",
    ("shipping", "dupsku"): "explicit",
    ("shipping", "dupsku2"): "explicit",
    ("shipping", "empty"): "competent",
    ("shipping", "freeedge"): "competent",
    ("shipping", "qtymult"): "competent",
    ("shipping", "qtyzero"): "careful",
    ("split", "capsmall"): "competent",
    ("split", "capvoucher"): "careful",
    ("split", "emptytip"): "competent",
    ("thermo", "round2"): "careful",
    ("thermo", "roundwin"): "careful",
    ("thermo", "sunday"): "careful",
    ("thermo", "wkendtz"): "careful",
}


#: per-level overrides (dom, cid, tier) from the same panel: the L3 brief words some monitor rules plainly enough that any
#: competent engineer handles them, and makes the storm rule fair (careful) where the L1 brief leaves it contested.
#: scrub pass-1 coverage checks (2026-11-15; not panel-rated): a rule the brief states, at its boundary or in a case the
#: drawn checks never built; `rerev` reads "as if that event never happened" for a reverse that is itself reversed
CHECK_TIER.update({("dj", "blendb"): "competent", ("invoices", "dueday"): "competent", ("rsvp", "noguests"): "competent",
                   ("ledger", "rerev"): "careful"})
CHECK_TIER.update({
    ("monitor", "batstorm", 3): "competent",
    ("monitor", "episode", 3): "competent",
    ("monitor", "prompt", 3): "competent",
    ("monitor", "storm", 3): "careful",
})


def check_tier(dom, cid, kind=None, tier=None):
    if kind == "x":
        return "explicit"
    return CHECK_TIER.get((dom, cid, tier), CHECK_TIER.get((dom, cid), "unrated"))


def _judge_all(inst: dict, code: str) -> dict:
    """-> {valid, q, cpu_s, note, results:[{id, pass, weight, kind, detail}]} for one answer (code = the module)."""
    pub, exps = inst["public"], inst["hidden"]["exps"]
    r = _run(code, inst)
    if not r["ok"]:
        return {"valid": False, "q": 0.0, "cpu_s": r["cpu_s"], "note": r["err"],
                "results": [{"id": e["id"], "pass": False, "weight": e["w"], "kind": e["kind"],
                             "detail": "invalid: " + r["err"][:80]} for e in exps]}
    J = _judges(inst)
    tot = got = 0.0
    results = []
    for e in exps:
        o = r["out"].get(e["id"])
        try:
            if _err(o) and (pub["domain"], e["id"]) in ERR_JUDGED:
                ok = "ValueError" in str(o["__err"])      # refusing bad input with a ValueError is an accepted answer
            else:
                ok = (not _err(o)) and bool(J[e["id"]](o, e["scen"], pub["params"]))
        except Exception:
            ok = False
        tot += e["w"]
        got += e["w"] if ok else 0
        results.append({"id": e["id"], "pass": ok, "weight": e["w"], "kind": e["kind"],
                        "check_tier": check_tier(pub["domain"], e["id"], e["kind"], pub["tier"]),
                        "detail": ("error in your code" if _err(o) else "ok" if ok else "wrong")
                        + f" ({e['kind']}, w={e['w']})"})
    q = round(got / tot, 4) if tot else 0.0
    return {"valid": True, "q": q, "cpu_s": r["cpu_s"],
            "note": "fail:" + ",".join(x["id"] + ("!" if _err(r["out"].get(x["id"])) else "")
                                       for x in results if not x["pass"]),
            "results": results}


def check(inst, code):
    """The ff contract: {"q", "valid", "cpu_s", "note"} (q = weighted share of expectations met)."""
    j = _judge_all(inst, code)
    return {k: j[k] for k in ("q", "valid", "cpu_s", "note")}


def points(q: float, q_base: float, valid: bool = True, q_ref: float = 1.0) -> float:
    if not valid:
        return 0.0
    if q_ref <= q_base:
        return POINTS_BASE
    return round(max(POINTS_FLOOR, POINTS_BASE + (POINTS_REF - POINTS_BASE) * (q - q_base) / (q_ref - q_base)), 1)


_CACHE: dict = {}
_LOCK = threading.Lock()


def _seed_for(gen: dict, release_seed: str, case_id: str) -> int:
    """``ffseed`` reproduces the fast-fail instance of that seed (domain = pick_domain(seed)); otherwise a
    64-bit int from the contract seed."""
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    if gen.get("deal"):
        return _dealt_seed(gen)
    return 1 + _gen.instance_int(FAMILY, release_seed, case_id, gen.get("level"), gen.get("key"))


#: seeds scanned to find the distinct briefs (prompt texts) of a (tier, domain) pool; every prompt variant of the bank shows
#: up well inside this span (monitor has the most)
POOL_SPAN = 3000


@functools.lru_cache(maxsize=None)
def _brief_pool(tier: int, dom) -> tuple:
    """-> tuple of tuples of seeds: one group per distinct brief text (prompt), each group = the seeds (ascending) that give
    that text; groups ordered by their first seed. Deterministic, release-independent."""
    groups: dict = {}
    for sd in range(1, POOL_SPAN + 1):
        groups.setdefault(make_instance(sd, tier, dom)["prompt"], []).append(sd)
    return tuple(tuple(v) for v in sorted(groups.values(), key=lambda v: v[0]))


#: domains a dealt set draws from. Tier 3 deals the tier-1 bank only: in the 4-model proxy runs (haiku, sonnet, grok, minimax)
#: monitor scored 27.0 for all four (below the 30 anchor, 6 of 6 sampled implied expectations failed by everyone, only the
#: four explicit rungs pass) and notify 30.0 for both models tried (3 of 6 failed by all, 3 passed by all): zero spread between
#: models, and monitor alone is 36 of the 60 tier-3 texts (60 %). They stay reachable by ``domain:`` pin and by the
#: undealt draw (pick_domain); they are only out of the deal.
DEAL_DOMS = {1: tuple(DOMS), 3: tuple(DOMS_BASE)}


@functools.lru_cache(maxsize=None)
def _deal_pool(tier: int, dom) -> tuple:
    """-> ((domain, (text group, ...)), ...): the dealt domains in a fixed order, each with its distinct brief texts (a text
    group = the ascending seeds giving that text), both ordered by first seed. ``dom`` pins one domain (any domain)."""
    doms = (dom,) if dom else DEAL_DOMS[tier]
    by_dom: dict = {d: {} for d in doms}
    for sd in range(1, POOL_SPAN + 1):
        d = dom or pick_domain(sd, tier)
        if d not in by_dom:
            continue
        inst = make_instance(sd, tier, d)
        # a text group = one brief text; the everyday domains (_implied_t1) carry a stated-rule variant `v` and their texts
        # also vary in function name and numbers, so they group by variant (each visit still draws a different seed of it)
        key = ("v", inst["public"]["params"]["v"]) if tier == 1 and "v" in inst["public"]["params"] else inst["prompt"]
        by_dom[d].setdefault(key, []).append(sd)
    return tuple((d, tuple(tuple(v) for v in sorted(g.values(), key=lambda v: v[0]))) for d, g in by_dom.items() if g)


#: chains packs ``items_per_prompt`` P consecutive slots (aligned at multiples of P) into one prompt but does not tell the
#: generator P (``generator.deal`` = {key, slot, of}). The deal therefore assumes P <= PACK_MAX and keeps a prompt from holding
#: one domain twice: the first PACK_MAX-1 domains of each block share no domain with the last PACK_MAX-1 of the block before
#: (a group of P <= PACK_MAX slots straddles at most one block boundary, taking a suffix of s <= 3 domains of one block and
#: a prefix of P-s <= 3 of the next). Needs 2*(PACK_MAX-1) <= domains; a smaller bank fails loudly. A ``domain`` pin repeats
#: on purpose and is exempt. If chains ever passes P, pass it here instead of assuming.
PACK_MAX = 4


@functools.lru_cache(maxsize=None)
def _domain_order(key, lv, pin, n, blk, pinned) -> tuple:
    """-> the visit order (indices into the dealt domains) of block ``blk``: a function of (key, level, pin, blk) only, so
    slots 1..n do not change when the set grows"""
    order = list(range(n))
    random.Random(f"{key}:implied:{lv}:{pin}:dom:{blk}").shuffle(order)
    if pinned or blk == 0:
        return tuple(order)
    g = PACK_MAX - 1
    if n < 2 * g:
        raise ValueError(f"implied deal: {n} domains cannot keep a packed prompt of up to {PACK_MAX} items free of a repeated "
                         f"domain (needs at least {2 * g}); add domains or pin one with `domain:`")
    prev = _domain_order(key, lv, pin, n, blk - 1, pinned)
    banned = set(prev[-g:])
    head = [i for i in order if i not in banned][:g]      # the first g slots avoid the last g of the block before
    return tuple(head + [i for i in order if i not in head])


def _dealt_seed(gen: dict) -> int:
    """``deal: true`` on a flat item (chains give ``generator.deal = {key, slot, of}``): the set's items are distinct briefs
    spread evenly over DOMAINS. Block ``b = slot // n_domains`` visits every dealt domain once, in a seeded order for that
    block; inside a domain the b-th visit takes the next text of a seeded without-replacement cycle over that domain's texts
    (reshuffled once exhausted), and the seed inside a text group moves on with the visit so a repeated text is a different
    scenario draw. The ``domain`` pin narrows the deal to that domain. Level 5 (one text) deals over its seed groups."""
    lv = int(gen.get("level", 1))
    if lv not in LEVELS:
        raise ValueError(f"implied: no level {lv} (levels {sorted(LEVELS)})")
    deal = gen["deal"]
    slot = int(deal.get("slot", 0))
    key = deal.get("key", "deal")
    tier = LEVELS[lv]["tier"]
    if tier not in DEAL_DOMS:
        pool = _brief_pool(tier, gen.get("domain"))
        blk, pos = divmod(slot, len(pool))
        order = list(range(len(pool)))
        random.Random(f"{key}:implied:{lv}:{gen.get('domain')}:{blk}").shuffle(order)
        grp = pool[order[pos]]
        return grp[blk % len(grp)]
    pool = _deal_pool(tier, gen.get("domain"))
    if not gen.get("domain") and len(pool) < 2 * (PACK_MAX - 1):
        raise ValueError(f"implied deal: {len(pool)} domains cannot keep a packed prompt of up to {PACK_MAX} items free of a "
                         f"repeated domain (needs at least {2 * (PACK_MAX - 1)}); add domains or pin one with `domain:`")
    blk, pos = divmod(slot, len(pool))
    dorder = _domain_order(key, lv, gen.get("domain"), len(pool), blk, bool(gen.get("domain")))
    dname, texts = pool[dorder[pos]]
    cyc, k = divmod(blk, len(texts))
    torder = list(range(len(texts)))
    random.Random(f"{key}:implied:{lv}:{dname}:txt:{cyc}").shuffle(torder)
    grp = texts[torder[k]]
    return grp[blk % len(grp)]


def _instance(seed: int, tier: int, dom=None, variant=None) -> dict:
    ck = (seed, tier, dom, variant)
    with _LOCK:
        if ck not in _CACHE:
            _CACHE[ck] = make_instance(seed, tier, dom, variant)
        return _CACHE[ck]


def _inst_of(gen_: dict, release_seed: str, case_id: str) -> dict:
    lv = int(gen_.get("level", 1))
    if lv not in LEVELS:
        raise ValueError(f"implied: no level {lv} (levels {sorted(LEVELS)})")
    return _instance(_seed_for(gen_, release_seed, case_id), LEVELS[lv]["tier"], _dom_of(gen_, lv), _variant_pin(gen_))


def _variant_pin(gen_: dict):
    """ffseed reproduces the fast-fail brief (variant 0); `rule_variant: n` pins one (not `variant`: that is chains' family-level key); otherwise the seed decides."""
    if gen_.get("rule_variant") is not None:
        return int(gen_["rule_variant"])
    return 0 if gen_.get("ffseed") is not None else None


def _dom_of(gen_: dict, lv: int):
    """the domain pin of a gen; at level 5 an `ffseed` pin (an ff instance) is the medication brief unless a domain is named"""
    dom = gen_.get("domain")
    if dom is None and LEVELS[lv]["tier"] == 5 and gen_.get("ffseed") is not None:
        return "meds3"
    return dom


@functools.lru_cache(maxsize=None)
def _q_base(seed: int, tier: int, dom, variant=None) -> float:
    """The anchor: the better (by this grader) of the literal-brief and the do-nothing solution (ff baseline())."""
    inst = _instance(seed, tier, dom, variant)
    return check(inst, baseline(inst))["q"]


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    lv = int(gen.get("level", 1))
    inst = _inst_of(gen, release_seed, case_id)
    seed = _seed_for(gen, release_seed, case_id)
    pub = inst["public"]
    return json.loads(json.dumps({
        "prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
        "grader": "implied",
        "grader_config": {"family": FAMILY, "seed": seed, "tier": LEVELS[lv]["tier"],
                          "domain": pub["domain"] if LEVELS[lv]["tier"] == 5 else gen.get("domain"), "cpu_s": CPU_S,
                          "variant": pub.get("params", {}).get("v"),
                          "check_tiers": {e["id"]: check_tier(pub["domain"], e["id"], e["kind"], pub["tier"]) for e in inst["hidden"]["exps"]}},
        "meta": {"domain": pub["domain"], "tier": pub["tier"], "n_exp": pub["n_exp"],
                 "n_scored": len(inst["hidden"]["exps"]),
                 "answer_tokens": LEVELS[lv]["answer_tokens"]},
        "difficulty": "hard"}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + globals()["reference_code"](_inst_of(gen, release_seed, case_id)) + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    inst = _inst_of(gen, release_seed, case_id)
    return ["```python\n" + f(inst) + "\n```" for f in (_literal, pad, null)]


GRADE_IS_ENGINE = True


def grade_implied(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "implied": cfg = {seed, tier, domain, cpu_s}. The reply's last python block is the module;
    ``results`` holds one entry per expectation (id, pass, weight, kind, detail) for per-expectation elements."""
    from .. import graders as _g
    inst = _instance(int(cfg["seed"]), int(cfg["tier"]), cfg.get("domain"), cfg.get("variant"))
    code = _g.extract_python(response_text or "", finish_reason)
    j = _judge_all(inst, code)
    if not j["valid"]:
        return {"grade": "fail", "detail": j["note"], "score": 0.0, "rate": 0.0, "points": 0.0, "q": 0.0,
                "cpu_s": j["cpu_s"], "results": j["results"]}
    qb = _q_base(int(cfg["seed"]), int(cfg["tier"]), cfg.get("domain"), cfg.get("variant"))
    pts = points(j["q"], qb)
    s = round(min(1.0, pts / POINTS_REF), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"{j['q']:.3f} of the expectation weight met (anchor {qb:.3f}); {pts} points; {j['note']}",
            "score": s, "rate": s, "points": pts, "q": j["q"], "anchor_q": qb, "cpu_s": j["cpu_s"],
            "results": j["results"]}


def grade(answer: str, materialized: dict) -> float:
    return float(grade_implied(answer, materialized["grader_config"])["score"])
