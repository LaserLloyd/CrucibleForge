"""IMPLIED domain bank, part 2: six more domains (meds, thermostat, splitter, backup, timesheet, notifier).
Same contract as implied.py builders: build_X(rng, tier) -> {text, fn, S, judges, params, FORCE, POOL}.
All scenarios are {"calls": [{"f": "main", "a": [args]}], "meta": {...}}; the driver returns one result per call.
FORCE = expectations always drawn (each is one a blanket "pad everything" solution gets WRONG);
POOL = the rest (drawn at random at tier 1, all at tier 3)."""
import random
from fractions import Fraction

BASE = 10080
DAY = 1440


def _L(day, h, m, tz):
    return BASE + day * DAY + h * 60 + m - tz


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _isint(x):
    return _num(x) and float(x) == int(x)


def _call(*a, f="main"):
    return {"f": f, "a": list(a)}


class _B:
    def __init__(self):
        self.S, self.J = {}, {}

    def add(self, i, calls, j, w, kind, why, **meta):
        self.S[i] = {"scen": {"calls": calls, "meta": meta}, "w": w, "kind": kind, "why": why}
        self.J[i] = j


def _out(b, text, fn, force, pool, params=None):
    return {"text": text, "fn": {"main": fn}, "S": b.S, "judges": b.J, "params": params or {}, "FORCE": force,
            "POOL": pool}


def _r0(o):
    return o[0] if isinstance(o, list) and len(o) == 1 else None


# ======================================================= MEDS
def _doses(r):
    if not isinstance(r, list):
        return None
    s = set()
    for d in r:
        if isinstance(d, dict):
            m, t = d.get("med", d.get("name")), d.get("time", d.get("at"))
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            m, t = d[0], d[1]
        else:
            return None
        if not _num(t):
            return None
        s.add((m, int(t)))
    return s


def _dose_list(r):
    """the doses in the order given: [(time, med)] (None if the shape is wrong)"""
    if not isinstance(r, list):
        return None
    out = []
    for d in r:
        if isinstance(d, dict):
            m, t = d.get("med", d.get("name")), d.get("time", d.get("at"))
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            m, t = d[0], d[1]
        else:
            return None
        if not _num(t):
            return None
        out.append((int(t), m))
    return out


def build_meds(rng, tier, v=0):
    fn = rng.choice(["due_doses", "pending_doses", "doses_due"])
    tz = rng.choice([-480, -300, 0, 60, 330, 540])
    nm = rng.sample(["Metformin", "Lisinopril", "Vitamin D", "Amoxicillin", "Sertraline", "Ibuprofen"], 3)
    A, B_, C = nm
    t1 = rng.choice([420, 480, 540])
    d = rng.randint(1, 3)
    b = _B()

    def have(r, m, t):
        s = _doses(r)
        return s is not None and (m, t) in s

    def S(meds, taken, now, tz_=None):
        return [_call(meds, taken, now, tz if tz_ is None else tz_)]
    if v == 1:
        b.add("x1", S([{"name": A, "times": [480, 530]}], [], _L(d, 9, 0, tz)),
              lambda o, sc, p: _doses(_r0(o)) == {(A, 480)}, .25, "x",
              "a dose 60 minutes late is listed, one only 10 minutes late is not yet")
    elif v == 2:
        order = sorted([(540, B_), (420, B_), (420, A)])
        b.add("x1", S([{"name": B_, "times": [540, 420]}, {"name": A, "times": [420]}], [], _L(d, 10, 0, tz)),
              lambda o, sc, p, order=order: _dose_list(_r0(o)) == order, .25, "x",
              "doses come back sorted by time, ties by medicine name")
    else:
        b.add("x1", S([{"name": A, "times": [t1]}], [], _L(d, 10, 0, tz)), lambda o, sc, p: have(_r0(o), A, t1), .25, "x",
              "a dose whose time has passed and was not taken is listed")
    b.add("x2", S([{"name": A, "times": [t1]}], [{"med": A, "at": _L(d, t1 // 60, 5, tz)}], _L(d, 10, 0, tz)),
          lambda o, sc, p: _doses(_r0(o)) == set(), .25, "x", "a dose that was taken is not listed")
    b.add("x3", S([{"name": A, "times": [t1, 1200]}], [], _L(d, 10, 0, tz)),
          lambda o, sc, p: _doses(_r0(o)) == {(A, t1)}, .25, "x", "a dose later today is not listed yet")
    b.add("yday", S([{"name": A, "times": [t1]}], [{"med": A, "at": _L(d - 1, t1 // 60, 2, tz)}], _L(d, 10, 0, tz)),
          lambda o, sc, p: have(_r0(o), A, t1), 1, "i", "yesterday's tap does not cover today's dose")
    tz2 = rng.choice([540, 600])
    b.add("tzday", S([{"name": A, "times": [480, 1410]}], [{"med": A, "at": _L(d - 1, 23, 40, tz2)}],
                     _L(d, 8, 30, tz2), tz2), lambda o, sc, p: have(_r0(o), A, 480), 1, "i",
          "my day starts at my local midnight, not UTC's")
    b.add("night", S([{"name": C, "times": [180, 1410]}], [], _L(d, 23, 50, tz)),
          lambda o, sc, p: _doses(_r0(o)) == {(C, 180), (C, 1410)}, 1, "a",
          "a 3am dose and an 11:30pm dose both still need a reminder: medicine matters more than sleep")
    if v == 1:
        b.add("graceedge", S([{"name": A, "times": [540]}], [], _L(d, 9, 15, tz)),
              lambda o, sc, p: _doses(_r0(o)) == {(A, 540)}, 1, "i", "a dose exactly 15 minutes late is listed")
    if v == 2:
        b.add("nightsort", S([{"name": C, "times": [1410, 180]}], [], _L(d, 23, 50, tz)),
              lambda o, sc, p: _dose_list(_r0(o)) == [(180, C), (1410, C)], 1, "i",
              "the sorted order holds across the whole day, not only for the first dose")
    when = ["whose time has already come", "whose time came at least 15 minutes ago",
            "whose time has already come"][v]
    srt = ", sorted by time (earliest first), ties by medicine name" if v == 2 else ""
    text = (f"Medication reminder for one person.\n\n    def {fn}(meds, taken, now, tz) -> list\n\n"
            "- meds: [{\"name\": str, \"times\": [minutes after local midnight]}] (every listed time is one dose, every day); "
            "taken: [{\"med\": name, \"at\": minute}] (each time the person tapped 'taken'); now: the current time; "
            "tz: the person's UTC offset in minutes (local time = UTC + tz).\n"
            "- All times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes).\n"
            "- Return the doses that need attention now: [{\"med\": name, \"time\": minute after local midnight}] for doses "
            f"scheduled on the person's current local day {when} and that have not been taken{srt}.")
    return _out(b, text, fn, ["night"], ["yday", "tzday"] + (["graceedge"] if v == 1 else []) + (["nightsort"] if v == 2 else []))


MEDS_REF = '''
def @FN@(meds, taken, now, tz):
    V = @V@
    G = 15 if V == 1 else 0
    DAY = 1440
    ln = now + tz
    mod = ln % DAY
    start = ln - mod - tz
    def norm(s): return str(s).strip().lower()
    out = []
    for m in meds:
        name = norm(m["name"]); times = sorted(m["times"])
        taps = sorted(t["at"] for t in taken if norm(t["med"]) == name and start <= t["at"] < start + DAY)
        used = set()
        for at in taps:
            loc = at - start; best = None
            for i, tm in enumerate(times):
                if i in used or tm > loc + 60: continue      # a tap covers a dose at most an hour ahead of it, never 8 h early
                if best is None or abs(tm - loc) < abs(times[best] - loc): best = i
            if best is not None: used.add(best)
        for i, tm in enumerate(times):
            if tm + G <= mod and i not in used:
                out.append({"med": m["name"], "time": tm})
    if V == 2: out.sort(key=lambda d: (d["time"], d["med"]))
    return out
'''
MEDS_ALT = '''
def @FN@(meds, taken, now, tz):
    V = @V@
    G = 15 if V == 1 else 0
    lm = (now + tz) % 1440
    midnight = now - lm
    res = []
    for m in meds:
        mine = sorted(t["at"] - midnight for t in taken if t["med"].lower().strip() == m["name"].lower().strip()
                      and 0 <= t["at"] - midnight < 1440)
        times = sorted(m["times"])
        covered = []
        for at in mine:                      # a tap covers the latest dose at or before it (else the first)
            c = [i for i, tm in enumerate(times) if tm <= at and i not in covered]
            if c: covered.append(c[-1])
            else:
                c = [i for i in range(len(times)) if i not in covered and times[i] <= at + 60]
                if c: covered.append(c[0])
        for i in range(len(times) - 1, -1, -1):
            if times[i] + G <= lm and i not in covered:
                res.append((m["name"], times[i], "due"))
    if V == 2: res.sort(key=lambda r: (r[1], r[0]))
    return res
'''
MEDS_LIT = '''
def @FN@(meds, taken, now, tz):
    V = @V@
    G = 15 if V == 1 else 0
    ln = now + tz
    day = ln // 1440; mod = ln % 1440
    out = []
    for m in meds:
        taps = [t for t in taken if t["med"] == m["name"] and t["at"] // 1440 == now // 1440]
        for tm in m["times"]:
            if tm + G <= mod and not taps:
                out.append({"med": m["name"], "time": tm})
    if V == 2: out.sort(key=lambda d: (d["time"], d["med"]))
    return out
'''
MEDS_PAD = MEDS_REF.replace("    out = []\n    for m in meds:", "    out = []\n    if mod >= 1320 or mod < 420:\n        return []\n    for m in meds:", 1)\
    .replace("if tm + G <= mod and i not in used:", "if tm + G <= mod and mod - tm <= 180 and i not in used:")\
    .replace("    return out\n", "    return out[:3]\n")
MEDS_NULL = "def @FN@(meds, taken, now, tz):\n    return []\n"


# ======================================================= THERMOSTAT
def _tt(sched, m):
    es = sorted(sched, key=lambda e: e["from"])
    cur = es[-1]
    for e in es:
        if e["from"] <= m:
            cur = e
    return cur["temp"]


def build_thermo(rng, tier, v=0):
    fn = rng.choice(["target_temp", "setpoint", "heating_target"])
    T1, T2 = rng.choice([20, 21, 22]), rng.choice([16, 17, 18])
    f1, f2 = rng.choice([360, 390, 420]), rng.choice([1320, 1350, 1380])
    tz = rng.choice([-480, -300, 0, 60, 330, 540])
    d = rng.randint(1, 3)
    sch = [{"from": f1, "temp": T1}, {"from": f2, "temp": T2}]
    b = _B()

    def val(r, t, tol=0.01):
        return _num(r) and abs(r - t) <= tol

    def S(sched, mins, win=False, tz_=None, day=None):
        z = tz if tz_ is None else tz_
        return [_call(sched, _L(d if day is None else day, mins // 60, mins % 60, z), z, win)]
    if v == 1:
        b.add("x1", S(sch, 720, day=5), lambda o, sc, p: val(_r0(o), T1 - 2), .25, "x",
              "on a Saturday the room is kept 2 degrees cooler")
    elif v == 2:
        b.add("x1", S([{"from": f1, "temp": 20.3}, {"from": f2, "temp": T2}], 720), lambda o, sc, p: val(_r0(o), 20.5), .25, "x",
              "the result is rounded to the nearest half degree: 20.3 becomes 20.5")
    else:
        b.add("x1", S(sch, 720), lambda o, sc, p: val(_r0(o), T1), .25, "x", "mid-day gives the day temperature")
    b.add("x2", S(sch, f2 + 30), lambda o, sc, p: val(_r0(o), T2), .25, "x", "evening gives the night temperature")
    b.add("x3", S(sch, f1), lambda o, sc, p: val(_r0(o), T1), .25, "x", "an entry applies from its own minute")
    b.add("wrap", S(sch, 180), lambda o, sc, p: val(_r0(o), T2), 1, "i",
          "at 3am the evening's setting is still running (the day does not restart cold)")
    # a time where UTC and local give different temperatures
    tzs = rng.sample([540, -480, 330, -300, 600], 5)
    pick = None
    for z in tzs:
        for lm in rng.sample(range(0, 1440, 30), 48):
            if _tt(sch, lm) != _tt(sch, (lm - z) % 1440):
                pick = (z, lm)
                break
        if pick:
            break
    z, lm = pick
    b.add("window", S(sch, 720, True), lambda o, sc, p: _num(_r0(o)) and 5 <= _r0(o) <= 10, 1, "a",
          "an open window stops the heating, but never below frost protection")
    low = rng.choice([0, 2, 3])
    b.add("frost", S([{"from": f1, "temp": T1}, {"from": f2, "temp": low}], 180),
          lambda o, sc, p: _num(_r0(o)) and 5 <= _r0(o) <= 12, 1, "i", "pipes freeze: never aim below about 5 C")
    b.add("warmhalf", S([{"from": f1, "temp": 24.5}, {"from": f2, "temp": T2}], 720), lambda o, sc, p: val(_r0(o), 24.5), 1, "a",
          "a warm room for a baby or an elderly person, in half degrees: 24.5 C is honoured, not rounded or capped")
    b.add("unsorted", S([{"from": f1, "temp": T1}, {"from": f2, "temp": T2}, {"from": 0, "temp": T2}], 720),
          lambda o, sc, p: val(_r0(o), T1), 1, "i", "people add schedule entries in any order")
    b.add("empty", S([], 720), lambda o, sc, p: _num(_r0(o)) and 5 <= _r0(o) <= 22, 1, "i",
          "no schedule yet: stay at a safe default, do not crash")
    if v == 1:
        zw = rng.choice([540, 600, 330])
        b.add("wkendtz", S(sch, 180, tz_=zw, day=5), lambda o, sc, p: val(_r0(o), T2 - 2), 1, "i",
              "the weekend is the person's local Saturday and Sunday, not UTC's")
        b.add("sunday", S(sch, 1380 + 5, day=6, tz_=0), lambda o, sc, p: val(_r0(o), T2 - 2), 1, "i",
              "Sunday counts as weekend too")
    if v == 2:
        b.add("round2", S([{"from": 0, "temp": 18.74}], 720), lambda o, sc, p: val(_r0(o), 18.5), 1, "i",
              "18.74 is closer to 18.5 than to 19")
        b.add("roundwin", S([{"from": 0, "temp": 22.0}], 720, True),
              lambda o, sc, p: _num(_r0(o)) and 5 <= _r0(o) <= 10 and abs(_r0(o) * 2 - round(_r0(o) * 2)) < 1e-9, 1, "i",
              "even with the window open the answer is a whole or half degree")
    extra = ["", "\n- On Saturdays and Sundays (the person's local days) the room is kept 2 degrees cooler than the schedule says "
             "(before the safety rules you decide yourself).",
             "\n- Round the result to the nearest half degree."][v]
    text = (f"Heating controller for one room.\n\n    def {fn}(schedule, now, tz, window_open) -> float\n\n"
            "- schedule: [{\"from\": minute after local midnight, \"temp\": degrees C}]; each entry applies from its time until "
            "the next entry's time.\n"
            "- now: the current time, integer minutes from Monday 00:00 UTC of some week (a day is 1440 minutes); tz: UTC offset "
            "in minutes (local = UTC + tz); window_open: bool.\n"
            "- Return the temperature (degrees C) the room should be heated to right now." + extra)
    return _out(b, text, fn, ["window", "warmhalf"], ["wrap", "frost", "unsorted", "empty"]
                + (["wkendtz", "sunday"] if v == 1 else []) + (["round2", "roundwin"] if v == 2 else []))


THERMO_REF = '''
def @FN@(schedule, now, tz, window_open):
    V = @V@
    m = (now + tz) % 1440
    es = sorted(schedule, key=lambda e: e["from"])
    t = 16.0
    if es:
        cur = es[-1]
        for e in es:
            if e["from"] <= m: cur = e
        t = float(cur["temp"])
    if V == 1 and ((now + tz) // 1440) % 7 >= 5: t -= 2
    t = max(5.0, t)
    if window_open: t = min(t, 7.0)
    if V == 2: t = round(t * 2) / 2
    return t
'''
THERMO_ALT = '''
def @FN@(schedule, now, tz, window_open):
    V = @V@
    m = (now + tz) % 1440
    best = None; gap = None
    for e in schedule:
        g = (m - e["from"]) % 1440
        if gap is None or g < gap: gap, best = g, e
    t = best["temp"] if best else 18
    if V == 1 and ((now + tz) // 1440) % 7 >= 5: t = t - 2
    if window_open: return 6.0
    return round(max(t, 6) * 2) / 2
'''
THERMO_LIT = '''
def @FN@(schedule, now, tz, window_open):
    V = @V@
    m = (now + tz) % 1440
    cur = None
    for e in schedule:
        if e["from"] <= m: cur = e["temp"]
    if cur is not None and V == 1 and ((now + tz) // 1440) % 7 >= 5: cur -= 2
    if cur is not None and V == 2: cur = round(cur * 2) / 2
    return cur
'''
THERMO_PAD = '''
def @FN@(schedule, now, tz, window_open):
    V = @V@
    m = (now + tz) % 1440
    es = sorted(schedule, key=lambda e: e["from"])
    t = 16
    if es:
        cur = es[-1]
        for e in es:
            if e["from"] <= m: cur = e
        t = cur["temp"]
    if V == 1 and ((now + tz) // 1440) % 7 >= 5: t -= 2
    if window_open: return 0
    return min(23, int(round(max(5, t))))
'''
THERMO_NULL = "def @FN@(schedule, now, tz, window_open):\n    return 0.0\n"


# ======================================================= BILL SPLITTER
def _sh(r):
    if not isinstance(r, dict) or not all(_num(v) for v in r.values()):
        return None
    return r


def build_split(rng, tier, v=0):
    fn = rng.choice(["split_bill", "divide_bill", "share_bill"])
    P = rng.sample(["Ana", "Ben", "Cy", "Dee", "Eli", "Fay", "Gus"], 7)
    b = _B()

    def S(items, people, tip):
        return [_call(items, people, tip)]

    def exactly(o, want, tol=0):
        # the brief returns "each an integer number of cents": the tolerance is for a rounding choice, never a part cent
        r = _sh(_r0(o))
        return r is not None and set(r) >= set(want) and all(_isint(r[k]) and abs(r[k] - v) <= tol for k, v in want.items())
    a, c = P[0], P[1]
    pa, pc = rng.choice([800, 1000, 1200]), rng.choice([2400, 3000, 3600])
    if v == 2:
        b.add("x1", S([{"name": "a", "price": 60000, "eaters": [a]}, {"name": "c", "price": 40000, "eaters": [c]}], [a, c], 20),
              lambda o, sc, p: exactly(o, {a: 63000, c: 42000}, .5), .25, "x",
              "a 20% tip on a big bill stops at 5000 cents in all, shared by what each ate")
    else:
        b.add("x1", S([{"name": "a", "price": pa, "eaters": [a]}, {"name": "c", "price": pc, "eaters": [c]}], [a, c], 0),
              lambda o, sc, p: exactly(o, {a: pa, c: pc}, .5), .25, "x", "each pays for what they ate")
    if v == 1:
        b.add("x2", S([{"name": "pizza", "price": 1200, "eaters": []}], [a, c], 0),
              lambda o, sc, p: exactly(o, {a: 600, c: 600}, .5), .25, "x", "an item with no eaters is shared by everyone at the table")
    else:
        b.add("x2", S([{"name": "pizza", "price": 1200, "eaters": [a, c]}], [a, c], 0),
              lambda o, sc, p: exactly(o, {a: 600, c: 600}, .5), .25, "x", "a shared dish is split equally")
    b.add("x3", S([{"name": "a", "price": 1000, "eaters": [a]}, {"name": "c", "price": 3000, "eaters": [c]}], [a, c], 10),
          lambda o, sc, p: exactly(o, {a: 1100, c: 3300}, .5), .25, "x", "the tip follows what each person ate")
    # float-prone equal split with tip
    items7 = [{"name": "pizza", "price": rng.choice([2999, 3001, 4003]), "eaters": P},
              {"name": "wine", "price": rng.choice([1001, 1303]), "eaters": P[:5]}]
    food = sum(i["price"] for i in items7)
    tipp = rng.choice([15, 18, 12])

    def tipsum(o, sc, p, food=food, tipp=tipp):
        r = _sh(_r0(o))
        if r is None or set(r) != set(P) or not all(_isint(v) for v in r.values()):
            return False
        tip = Fraction(food * tipp, 100)
        return int(tip) + food - 0 <= sum(r.values()) <= -(-tip.numerator // tip.denominator) + food
    b.add("tipsum", S(items7, P, tipp), tipsum, 1, "i", "the shares add up to the bill plus tip, to the cent")
    def np2(o, sc, p):
        r = _sh(_r0(o))
        if r is None or set(r) != set(P[:3]) or not all(_isint(v) for v in r.values()):
            return False
        tot = 3004 + 450.6
        return (r[P[2]] == 0 and int(tot) <= sum(r.values()) <= int(tot) + 1 and abs(r[P[0]] - 500.5 * 1.15) <= 1
                and abs(r[P[1]] - 2503.5 * 1.15) <= 1)
    b.add("nonpayer", S([{"name": "dish", "price": 1001, "eaters": P[:2]}, {"name": "b", "price": 2003, "eaters": [P[1]]}],
                        P[:3], 15), np2, 1, "i",
          "someone who ate nothing pays nothing (tip included), and odd cents still add up to the bill plus tip")
    n = rng.randint(5, 7)
    price = n * 100 + n - 2
    ex = price * 1.1 / n

    def spread(o, sc, p, n=n, price=price, ex=ex):
        r = _sh(_r0(o))
        return (r is not None and set(r) == set(P[:n]) and all(_isint(v) for v in r.values())
                and price * 1.1 - 1 <= sum(r.values()) <= price * 1.1 + 1 and all(abs(v - ex) < 1.5 for v in r.values()))
    b.add("spread", S([{"name": "feast", "price": price, "eaters": P[:n]}], P[:n], 10), spread, 1, "a",
          "leftover cents (with tip) are spread around, nobody is billed extra just for being first")
    b.add("discount", S([{"name": "a", "price": 3000, "eaters": [P[0]]}, {"name": "b", "price": 2000, "eaters": [P[1]]},
                         {"name": "voucher", "price": -1000, "eaters": P[:2]}], P[:2], 0),
          lambda o, sc, p: exactly(o, {P[0]: 2500, P[1]: 1500}), 1, "a",
          "a voucher (negative price) reduces the bill; it is not ignored")
    if v == 1:
        b.add("emptytip", S([{"name": "a", "price": 1000, "eaters": [a]}, {"name": "rice", "price": 600, "eaters": []}], [a, c], 10),
              lambda o, sc, p: exactly(o, {a: 1430, c: 330}, .5), 1, "i",
              "the shared item and the tip follow the same rules: c pays a half of the rice plus tip, nothing else")
    if v == 2:
        b.add("capsmall", S([{"name": "a", "price": 12000, "eaters": [a]}, {"name": "c", "price": 8000, "eaters": [c]}], [a, c], 20),
              lambda o, sc, p: exactly(o, {a: 14400, c: 9600}, .5), 1, "i",
              "a tip under the cap is paid in full")
        b.add("capvoucher", S([{"name": "a", "price": 80000, "eaters": [a]}, {"name": "c", "price": 40000, "eaters": [c]},
                               {"name": "voucher", "price": -20000, "eaters": [a, c]}], [a, c], 20),
              lambda o, sc, p: exactly(o, {a: 73500, c: 31500}, 1.5), 1, "i",
              "the cap counts the bill after the voucher: 100000 cents of food, so the 5000 cap applies")
    sx = ["", "\nAn item with no eaters is shared by everyone in `people`.",
          "\nThe tip is tip_pct percent of the food total but never more than 5000 cents in all."][v]
    text = (f"Restaurant bill splitter.\n\n    def {fn}(items, people, tip_pct) -> dict\n\n"
            "- items: [{\"name\": str, \"price\": integer cents, \"eaters\": [names]}]; each item's price is shared equally by its "
            "eaters. people: everyone at the table. tip_pct: a percentage tip on the food total, shared in proportion to what "
            "each person's food costs.\n- Return {person: how much they pay} with one entry for every name in `people`, each an integer number of cents." + sx)
    return _out(b, text, fn, ["spread", "discount"], ["tipsum", "nonpayer"] + (["emptytip"] if v == 1 else [])
                + (["capsmall", "capvoucher"] if v == 2 else []))


SPLIT_REF = '''
from fractions import Fraction as Fr
def @FN@(items, people, tip_pct):
    V = @V@
    owe = {p: Fr(0) for p in people}
    for it in items:
        for e in it["eaters"]: owe.setdefault(e, Fr(0))
    for it in items:
        eat = list(it["eaters"]) or list(people)
        for e in eat: owe[e] += Fr(it["price"], len(eat))
    f = (100 + Fr(tip_pct)) / 100
    food = sum(owe.values())
    if V == 2 and food > 0 and food * Fr(tip_pct) / 100 > 5000: f = 1 + Fr(5000) / food
    exact = {p: owe[p] * f for p in owe}
    total = round(sum(exact.values()))
    out = {p: int(v // 1) for p, v in exact.items()}
    people = list(owe)
    rem = total - sum(out.values())
    for p in sorted(owe, key=lambda p: out[p] - exact[p])[:max(rem, 0)]:
        out[p] += 1
    return out
'''
SPLIT_ALT = '''
def @FN@(items, people, tip_pct):
    V = @V@
    cents = {p: 0 for p in people}
    for it in items:
        for e in it["eaters"]: cents.setdefault(e, 0)
    for it in items:
        eat = list(it["eaters"]) or list(people)
        base, rem = divmod(it["price"], len(eat))
        for i, e in enumerate(eat):
            cents[e] += base + (1 if i < rem else 0)
    food = sum(cents.values())
    if V == 2 and food > 0 and (food * tip_pct + 50) // 100 > 5000:
        tip = 5000
        out = {p: cents[p] + (cents[p] * tip + food // 2) // food for p in cents}
        d = food + tip - sum(out.values())
        if d and out:
            k = max(out, key=out.get); out[k] += d
        return out
    target = food + (food * tip_pct + 50) // 100
    out = {p: cents[p] + (cents[p] * tip_pct + 50) // 100 for p in cents}
    d = target - sum(out.values())
    if d and out:
        k = max(out, key=out.get); out[k] += d
    return out
'''
SPLIT_LIT = '''
def @FN@(items, people, tip_pct):
    V = @V@
    owe = {p: 0.0 for p in people}
    for it in items:
        eat = it["eaters"] or (list(people) if V == 1 else [])
        for e in eat:
            owe[e] += it["price"] / len(eat)
    if V == 2:
        food = sum(it["price"] for it in items)
        if food > 0 and food * tip_pct / 100 > 5000: tip_pct = 500000 / food
    return {p: owe[p] * (1 + tip_pct / 100) for p in people}
'''
SPLIT_PAD = '''
def @FN@(items, people, tip_pct):
    V = @V@
    if V == 2:
        _f = sum(it["price"] for it in items)
        if _f > 0 and _f * tip_pct > 500000: tip_pct = 500000 / _f
    owe = {p: 0.0 for p in people}
    for it in items:
        eat = [e for e in it["eaters"] if e in owe] or list(people)
        pr = max(0, it["price"])
        for e in eat: owe[e] += pr / len(eat)
    out = {p: int(round(v * (100 + tip_pct) / 100)) for p, v in owe.items()}
    food = sum(max(0, it["price"]) for it in items)
    target = food + int(round(food * tip_pct / 100))
    if people:
        out[people[0]] += target - sum(out.values())
    return out
'''
SPLIT_NULL = "def @FN@(items, people, tip_pct):\n    return {}\n"


# ======================================================= BACKUP ROTATION
def _ids(r):
    if not isinstance(r, (list, tuple, set)):
        return None
    try:
        return set(r)
    except TypeError:
        return None


def build_backup(rng, tier, v=0):
    fn = rng.choice(["backups_to_delete", "prune_backups", "select_deletions"])
    now = _L(rng.randint(30, 40), rng.randint(0, 23), rng.randint(0, 59), 0)
    b = _B()
    ctr = [0]

    def mk(age_days, ok=True, size=500, pinned=False):
        ctr[0] += 1
        d = {"id": f"bk{ctr[0]:02d}", "time": now - int(age_days * DAY), "ok": ok, "size_mb": size}
        if pinned:
            d["pinned"] = True
        return d

    def build(spec, free=100000, need=1000):
        ctr[0] = 0
        bs = [mk(*s) for s in spec]
        rng.shuffle(bs)
        return bs, free, need

    def S(spec, free=100000, need=1000):
        bs, f, n = build(spec, free, need)
        return [_call(bs, now, f, n)], {bs_["id"]: i for bs_ in bs for i in [None]}, bs

    def add(i, spec, j, w, kind, why, free=100000, need=1000):
        bs, f, n = build(spec, free, need)
        meta = {"ok": {x["id"]: x["ok"] for x in bs}, "size": {x["id"]: x["size_mb"] for x in bs},
                "age": {x["id"]: round((now - x["time"]) / DAY, 2) for x in bs}, "free": f, "need": n}
        if v == 1:
            meta["pinned"] = {x["id"]: bool(x.get("pinned")) for x in bs}
        b.add(i, [_call(bs, now, f, n)], j, w, kind, why, **meta)

    def by_age(sc, lo, hi=None):
        m = sc["meta"]["age"]
        return [i for i, a in m.items() if a >= lo and (hi is None or a < hi)]

    def oneat(sc, age):
        return [i for i, a in sc["meta"]["age"].items() if int(a) == int(age)][0]
    ra = lambda lo: rng.uniform(lo + .1, lo + .9)
    rec = [(ra(1), True), (ra(2), True), (ra(3), True)]
    base = rec + [(ra(10), True), (ra(12), True), (ra(30), True)]

    def x1(o, sc, p):
        d = _ids(_r0(o))
        return d is not None and not (d & set(by_age(sc, 0, 7)))
    if v == 1:
        def xpin(o, sc, p):
            d = _ids(_r0(o))
            pin = [i for i, pp in sc["meta"]["pinned"].items() if pp][0]
            return d is not None and pin not in d and oneat(sc, 12) in d and not (d & set(by_age(sc, 0, 7)))
        add("x1", rec + [(ra(10), True), (ra(12), True), (45.5, True, 500, True)], xpin, .25, "x",
            "a pinned backup is never deleted, even when it is far past its time")
    elif v == 2:
        def xbad(o, sc, p):
            d = _ids(_r0(o))
            bad = [i for i, okk in sc["meta"]["ok"].items() if not okk and sc["meta"]["age"][i] >= 7]
            return d is not None and bad and set(bad) <= d and not (d & set(by_age(sc, 0, 7)))
        add("x1", rec + [(ra(8), False), (ra(10), True), (ra(16), False), (ra(17), True)], xbad, .25, "x",
            "a corrupt backup older than 7 days is deleted")
    else:
        add("x1", base, x1, .25, "x", "recent backups are kept")
    add("x2", base, lambda o, sc, p: _ids(_r0(o)) is not None and oneat(sc, 30) in _ids(_r0(o)), .25, "x",
        "a month-old backup is deleted when newer ones exist")
    add("x3", base, lambda o, sc, p: _ids(_r0(o)) is not None and oneat(sc, 12) in _ids(_r0(o))
        and oneat(sc, 10) not in _ids(_r0(o)), .25, "x", "of one older week, the newest is kept and the rest go")

    def lastgood(o, sc, p):
        d = _ids(_r0(o))
        return d is not None and oneat(sc, 35) not in d and oneat(sc, 60) in d
    add("lastgood", [(ra(1), False), (ra(2), False), (ra(3), False), (35.5, True), (60.5, True)], lastgood, 1, "i",
        "the recent backups are broken: the newest working copy must survive, even though it is old")

    def only(o, sc, p):
        d = _ids(_r0(o))
        return d is not None and oneat(sc, 40) not in d and oneat(sc, 50) in d
    add("onlycopy", [(40.5, True), (45.5, True), (50.5, True)], only, 1, "a",
        "when everything is old, the newest working copy is still kept (never delete the only good copy)")

    def space(o, sc, p):
        d = _ids(_r0(o))
        m = sc["meta"]
        if d is None:
            return False
        freed = sum(m["size"].get(i, 0) for i in d)
        newest = min(m["age"], key=m["age"].get)
        return m["free"] + freed >= m["need"] and newest not in d and len(d) < len(m["age"])
    add("nospace", [(ra(i), True) for i in range(1, 6)], space, 1, "a",
        "tonight's backup needs room: if retention rules leave the disk too full, delete the oldest more, never the newest",
        free=200, need=1200)

    def wkskew(o, sc, p):
        d = _ids(_r0(o))
        if d is None:
            return False
        kept = [i for i in by_age(sc, 7, 14) if i not in d]
        fu = [i for i, a in sc["meta"]["age"].items() if a < 0]
        return (len(kept) == 1 and sc["meta"]["ok"][kept[0]] and not (d & set(fu)) and oneat(sc, 30) in d)
    add("weeklykeeper", [(ra(1), True), (ra(7) + .2, False), (ra(10) - .3, True), (ra(12) + .3, True), (-0.5, True),
                         (30.5, True)], wkskew, 1, "i",
        "the week's kept backup must be a working one, and a backup stamped in the future (clock skew) is kept, not treated as ancient")
    if v == 1:
        def pspace(o, sc, p):
            d = _ids(_r0(o))
            m = sc["meta"]
            if d is None:
                return False
            pin = [i for i, pp in m["pinned"].items() if pp][0]
            freed = sum(m["size"].get(i, 0) for i in d)
            newest = min(m["age"], key=m["age"].get)
            return pin not in d and newest not in d and m["free"] + freed >= m["need"]
        add("pinnedspace", [(ra(i), True) for i in range(1, 6)] + [(50.5, True, 500, True)], pspace, 1, "a",
            "when tonight's backup needs room, pinned backups are still not touched: older ordinary ones go instead",
            free=200, need=1200)
    if v == 2:
        def clone(o, sc, p):
            d = _ids(_r0(o))
            bad = [i for i, okk in sc["meta"]["ok"].items() if not okk and sc["meta"]["age"][i] >= 7]
            good14 = [i for i, a in sc["meta"]["age"].items() if 14 <= a < 21 and sc["meta"]["ok"][i]]
            return d is not None and set(bad) <= d and len(good14) == 1 and good14[0] not in d
        add("corruptlone", [(ra(1), True), (ra(8), False), (ra(9), False), (ra(15), True)], clone, 1, "i",
            "corrupt copies never stand in for a week's backup: the one good older backup is kept, the corrupt ones go")
    bx = ["", "\n- A backup with \"pinned\": True is never deleted, whatever its age or the disk space.",
          "\n- A corrupt backup (ok = False) older than 7 days is always deleted."][v]
    text = (f"Nightly backup rotation.\n\n    def {fn}(backups, now, free_mb, min_free_mb) -> list\n\n"
            "- backups: [{\"id\": str, \"time\": minute, \"ok\": bool (False = the backup is corrupt), \"size_mb\": int}] in no "
            "particular order; now: current time (minutes; a day is 1440).\n"
            "- Return the ids of backups to delete. Keep every backup from the last 7 days. Of older ones keep only the newest "
            "backup in each 7-day block (age 7-14 days, 14-21 days, 21-28 days); backups older than 28 days can normally be deleted to save space.\n"
            "- free_mb is the disk space free now; the next nightly backup needs min_free_mb free to run." + bx)
    # lastgood / onlycopy: re-added after the 3-reader Opus panel (F-055: 3 of 3 list "keep the newest good backup whatever its age",
    # reading this wording); they are 'only a careful one' checks, not contestable ones
    return _out(b, text, fn, ["onlycopy", "nospace", "lastgood"],
                ["weeklykeeper"] + (["pinnedspace"] if v == 1 else []) + (["corruptlone"] if v == 2 else []))


BACKUP_REF = '''
def @FN@(backups, now, free_mb, min_free_mb):
    V = @V@
    DAY = 1440
    age = lambda b: (now - b["time"]) / DAY
    keep = {b["id"] for b in backups if age(b) < 7}
    if V == 1: keep |= {b["id"] for b in backups if b.get("pinned")}
    for k in (1, 2, 3):
        blk = [b for b in backups if 7 * k <= age(b) < 7 * (k + 1) and b["ok"]]
        if blk: keep.add(max(blk, key=lambda b: b["time"])["id"])
    good = [b for b in backups if b["ok"]]
    pool = good or backups
    newest = max(pool, key=lambda b: b["time"])["id"] if pool else None
    if newest: keep.add(newest)
    dele = {b["id"] for b in backups if b["id"] not in keep}
    size = {b["id"]: b["size_mb"] for b in backups}
    free = free_mb + sum(size[i] for i in dele)
    for b in sorted(backups, key=lambda b: b["time"]):
        if free >= min_free_mb: break
        if b["id"] in keep and b["id"] != newest and b["id"] not in dele and not (V == 1 and b.get("pinned")):
            dele.add(b["id"]); free += b["size_mb"]
    return sorted(dele)
'''
BACKUP_ALT = '''
def @FN@(backups, now, free_mb, min_free_mb):
    V = @V@
    D = 1440
    bs = sorted(backups, key=lambda b: -b["time"])
    keep = [b for b in bs if V == 1 and b.get("pinned")]
    seen_blocks = set()
    newest_good = next((b for b in bs if b["ok"]), bs[0] if bs else None)
    for b in bs:
        a = (now - b["time"]) // D
        if b in keep: continue
        if a < 7:
            if b["ok"] or b is newest_good: keep.append(b)
        elif a < 28 and b["ok"]:
            blk = a // 7
            if blk not in seen_blocks:
                seen_blocks.add(blk); keep.append(b)
    if newest_good and newest_good not in keep: keep.append(newest_good)
    free = free_mb + sum(b["size_mb"] for b in backups if b not in keep)
    for b in sorted(keep, key=lambda b: b["time"]):
        if free >= min_free_mb: break
        if b is not newest_good and not (V == 1 and b.get("pinned")):
            keep.remove(b); free += b["size_mb"]
    return tuple(b["id"] for b in backups if b not in keep)
'''
BACKUP_LIT = '''
def @FN@(backups, now, free_mb, min_free_mb):
    V = @V@
    DAY = 1440
    age = lambda b: (now - b["time"]) / DAY
    keep = {b["id"] for b in backups if age(b) < 7}
    if V == 1: keep |= {b["id"] for b in backups if b.get("pinned")}
    for k in (1, 2, 3):
        blk = [b for b in backups if 7 * k <= age(b) < 7 * (k + 1) and (b["ok"] or V != 2)]
        if blk: keep.add(max(blk, key=lambda b: b["time"])["id"])
    return [b["id"] for b in backups if b["id"] not in keep]
'''
BACKUP_PAD = '''
def @FN@(backups, now, free_mb, min_free_mb):
    V = @V@
    DAY = 1440
    age = lambda b: (now - b["time"]) / DAY
    keep = {b["id"] for b in backups if age(b) < 7}
    if V == 1: keep |= {b["id"] for b in backups if b.get("pinned")}
    for k in (1, 2, 3):
        blk = [b for b in backups if 7 * k <= age(b) < 7 * (k + 1) and b["ok"]]
        if blk: keep.add(max(blk, key=lambda b: b["time"])["id"])
    return [b["id"] for b in backups if b["id"] not in keep]
'''
BACKUP_NULL = "def @FN@(backups, now, free_mb, min_free_mb):\n    return []\n"


# ======================================================= TIMESHEET
def build_pay(rng, tier, v=0):
    fn = rng.choice(["weekly_pay", "total_pay", "pay_for_week"])
    rate = rng.choice([1500, 1650, 1999, 2250])
    b = _B()
    d0 = rng.randint(0, 3) * DAY
    H = 60

    def sh(s, e, un=None):
        d = {"start": d0 + s, "end": d0 + e}
        if un is not None:
            d["unpaid"] = un
        return d

    def S(shifts, r=rate):
        return [_call(shifts, r)]

    def pay_ok(o, mins, tol=1):
        r = _r0(o)
        return _isint(r) and abs(r - rate * mins / 60) <= tol

    if v == 1:
        b.add("x1", S([sh(9 * H, 17 * H, 30)]), lambda o, sc, p: pay_ok(o, 450), .25, "x",
              "a 30-minute unpaid break inside an 8-hour shift is not paid")
    elif v == 2:
        wk = [{"start": k * DAY + 9 * H, "end": k * DAY + 18 * H} for k in range(5)]
        b.add("x1", S(wk), lambda o, sc, p: pay_ok(o, 2850, 1.5), .25, "x",
              "45 hours in the week: the last 5 hours are paid at 1.5 times")
    else:
        b.add("x1", S([sh(9 * H, 17 * H)]), lambda o, sc, p: pay_ok(o, 480), .25, "x", "eight hours at the hourly rate")
    b.add("x2", S([sh(9 * H, 13 * H), sh(14 * H, 17 * H + 30)]), lambda o, sc, p: pay_ok(o, 450), .25, "x",
          "separate shifts add up")
    b.add("x3", S([sh(22 * H, 30 * H)]), lambda o, sc, p: pay_ok(o, 480), .25, "x", "a night shift across midnight")
    b.add("dupe", S([sh(9 * H, 17 * H), sh(9 * H, 17 * H)]), lambda o, sc, p: pay_ok(o, 480), 1, "i",
          "a double tap on 'clock in' must not pay the same hours twice")
    b.add("overlap", S([sh(9 * H, 13 * H + 17), sh(12 * H, 17 * H + 41)]), lambda o, sc, p: pay_ok(o, 521), 1, "i",
          "two entries that overlap are paid once for the overlap, by the minute")
    def bw(o, sc, p):
        if isinstance(o, dict) and "ValueError" in str(o.get("__err", "")):
            return True
        # ignore the bad shift (8 h paid) or read it as running past midnight (24 h); anything between pays the
        # reversed shift as hours worked (ds8b: 24000 = 16 h at 1500), which the why text rules out (FIXNOW 17, 2026-11-15)
        r = _r0(o)
        return _isint(r) and (abs(r - rate * 8) <= 1 or abs(r - rate * 24) <= 1)
    b.add("backwards", S([sh(9 * H, 17 * H), sh(17 * H, 9 * H)]),
          bw, 1, "i",
          "a clock-out earlier than the clock-in is a mistake: reject it (ValueError) or ignore that shift, but never take pay away")
    b.add("odd", S([sh(9 * H, 9 * H + 10), sh(11 * H, 11 * H + 37), sh(13 * H, 28 * H + 13)]),
          lambda o, sc, p: pay_ok(o, 10 + 37 + 15 * 60 + 13), 1, "a",
          "ten minutes of work is paid, a 15-hour festival day is paid in full, and pay is by the minute (no quarter hours, no cap)")
    if v == 1:
        b.add("bigbreak", S([sh(9 * H, 10 * H, 90), sh(12 * H, 14 * H)]), lambda o, sc, p: pay_ok(o, 120), 1, "i",
              "a break longer than the shift cannot make the shift pay negative")
    if v == 2:
        wk2 = [{"start": k * DAY + 9 * H, "end": k * DAY + 18 * H} for k in range(5)]
        b.add("otdupe", S(wk2 + [dict(wk2[0])]), lambda o, sc, p: pay_ok(o, 2850, 1.5), 1, "i",
              "a shift entered twice is still one shift: no extra overtime")
    px = ["", "\n- A shift may carry \"unpaid\": minutes of unpaid break inside it; those minutes are not paid.",
          "\n- Minutes beyond 2400 (40 hours) in the week are paid at 1.5 times the hourly rate."][v]
    text = (f"Payroll helper for one employee.\n\n    def {fn}(shifts, rate_cents_per_hour) -> int\n\n"
            "- shifts: [{\"start\": minute, \"end\": minute}] (clock-in and clock-out). Times are integer minutes counted from the start "
            "of the week (0 = Monday 00:00; a day is 1440), so a shift can run past midnight and its end can be above 1440. "
            "Example: Tuesday 22:00 to Wednesday 06:00 is {\"start\": 2760, \"end\": 3240}.\n"
            "- Pay is by the minute worked at the hourly rate. Return the total pay for the week in integer cents." + px)
    return _out(b, text, fn, ["odd"], ["dupe", "overlap", "backwards"] + (["bigbreak"] if v == 1 else [])
                + (["otdupe"] if v == 2 else []))


PAY_REF = '''
def @FN@(shifts, rate):
    V = @V@
    iv = sorted((s["start"], s["end"]) for s in shifts if s["end"] > s["start"])
    tot = 0; cs, ce = None, None
    for s, e in iv:
        if ce is None or s > ce:
            if ce is not None: tot += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    if ce is not None: tot += ce - cs
    if V == 1:
        tot -= sum(min(s.get("unpaid") or 0, s["end"] - s["start"]) for s in shifts if s["end"] > s["start"])
    if V == 2 and tot > 2400:
        return (2 * rate * (3 * tot - 2400) + 120) // 240
    return (2 * rate * tot + 60) // 120
'''
PAY_ALT = '''
def @FN@(shifts, rate_cents_per_hour):
    V = @V@
    mins = set()
    off = 0
    for s in shifts:
        a, b = s["start"], s["end"]
        if b < a: b += 1440
        mins.update(range(a, b))
        if V == 1 and b > a: off += min(s.get("unpaid") or 0, b - a)
    n = len(mins) - off
    if V == 2 and n > 2400: n = n + (n - 2400) / 2.0
    return int(round(n * rate_cents_per_hour / 60.0))
'''
PAY_LIT = '''
def @FN@(shifts, rate):
    V = @V@
    if V == 2:
        tot = sum(s["end"] - s["start"] for s in shifts)
        if tot > 2400: tot = tot + (tot - 2400) / 2
        return int(round(tot * rate / 60))
    return sum(int(round((s["end"] - s["start"] - ((s.get("unpaid") or 0) if V == 1 else 0)) * rate / 60)) for s in shifts)
'''
PAY_PAD = '''
def @FN@(shifts, rate):
    V = @V@
    iv = sorted((s["start"], s["start"] + min(720, 15 * round((s["end"] - s["start"]) / 15))) for s in shifts
                if s["end"] > s["start"])
    tot = 0; cs, ce = None, None
    for s, e in iv:
        if ce is None or s > ce:
            if ce is not None: tot += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    if ce is not None: tot += ce - cs
    if V == 1: tot -= sum(min(s.get("unpaid") or 0, s["end"] - s["start"]) for s in shifts if s["end"] > s["start"])
    if V == 2 and tot > 2400: tot = tot + (tot - 2400) / 2
    return int(round(rate * tot / 60))
'''.replace("(shifts, rate)", "(shifts, rate_cents_per_hour)").replace("rate * tot", "rate_cents_per_hour * tot")
PAY_NULL = "def @FN@(shifts, rate):\n    return 0\n"


# ======================================================= NOTIFIER
def build_notify(rng, tier):
    fn = rng.choice(["should_send", "allow_push", "may_notify"])
    tz = rng.choice([-480, -300, 0, 60, 330, 540])
    d = rng.randint(1, 3)
    b = _B()

    def ev(key, t, sev="info"):
        e = {"key": key, "t": t}
        if sev:
            e["severity"] = sev
        return e

    def sent(*pairs):
        return [{"key": k, "t": t} for k, t in pairs]

    def S(sn, e, z=None):
        return [_call(sn, e, tz if z is None else z)]

    def isb(v):
        return lambda o, sc, p: _r0(o) in (True, False) and bool(_r0(o)) is v
    noon = _L(d, 12, 0, tz)
    b.add("x1", S([], ev("k1", noon)), isb(True), .25, "x", "a first notification goes out")
    b.add("x2", S(sent(("a", noon - 30), ("b", noon - 20), ("c", noon - 10)), ev("k9", noon)), isb(False), .25, "x",
          "a fourth within the hour is held back")
    b.add("x3", S(sent(("a", noon - 130), ("b", noon - 110), ("c", noon - 100)), ev("k9", noon)), isb(True), .25, "x",
          "old notifications no longer count against the limit")
    n3 = _L(d, 3, 0, tz)
    b.add("quiet", S([], ev("k1", n3)), isb(False), 1, "i", "nobody wants a routine buzz at 3am")
    tzq = rng.choice([540, -480])
    b.add("tzquiet", S([], ev("k1", _L(d, 2, 0, tzq)), tzq), isb(False), 1, "i",
          "2am for the person is quiet time, whatever UTC says")
    tzd = rng.choice([-480, -420])
    b.add("tzday", S([], ev("k1", _L(d, 14, 0, tzd)), tzd), isb(True), 1, "i",
          "2pm for the person is daytime even when UTC says late evening")
    b.add("dup", S(sent(("k1", noon - 5)), ev("k1", noon)), isb(False), 1, "i", "the same message again 5 minutes later is spam")
    b.add("dupold", S(sent(("k1", noon - 120)), ev("k1", noon)), isb(True), 1, "i",
          "the same message two hours later is a fresh reminder")
    b.add("nosevquiet", S([], ev("k1", n3, None)), isb(False), 1, "i",
          "an event with no severity is treated as routine, not as an emergency")
    b.add("critnight", S([], ev("k1", n3, "critical")), isb(True), 1, "a", "an emergency wakes people up")
    b.add("critcap", S(sent(("a", noon - 20), ("b", noon - 15), ("c", noon - 10)), ev("k9", noon, "critical")), isb(True), 1,
          "a", "an emergency is sent even when the hourly limit is used up")
    b.add("critdup", S(sent(("k1", noon - 3)), ev("k1", noon, "critical")), isb(True), 1, "a",
          "an emergency that is still going is sent again, it is not swallowed as a duplicate")
    text = (f"Push-notification gate for a phone app.\n\n    def {fn}(sent, event, tz) -> bool\n\n"
            "- Return True if `event` should be pushed to the user now. sent: notifications already pushed to this user: "
            "[{\"key\": str, \"t\": minute}]; event: {\"key\": str, \"t\": minute, \"severity\": \"info\" or \"critical\"}; "
            "tz: the user's UTC offset in minutes (local = UTC + tz).\n"
            "- Times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes).\n"
            "- Rule: at most 3 notifications in any rolling 60 minutes.")
    return _out(b, text, fn, ["critnight", "critcap", "critdup"], ["quiet", "tzquiet", "tzday", "dup", "dupold", "nosevquiet"])


NOTE_REF = '''
def @FN@(sent, event, tz):
    t = event["t"]
    if event.get("severity") == "critical": return True
    lm = (t + tz) % 1440
    if lm >= 1320 or lm < 420: return False
    if any(s["key"] == event["key"] and 0 <= t - s["t"] < 10 for s in sent): return False
    return sum(1 for s in sent if 0 <= t - s["t"] < 60) < 3
'''
NOTE_ALT = '''
def @FN@(sent, event, tz):
    t = event["t"]
    if event.get("severity", "info") == "critical":
        return not any(s["key"] == event["key"] and t - s["t"] < 1 for s in sent)
    h = ((t + tz) % 1440) / 60.0
    if h >= 23 or h < 6.5: return False
    recent = [s for s in sent if t - 15 < s["t"] <= t]
    if any(s["key"] == event["key"] for s in recent): return False
    return len([s for s in sent if t - 60 < s["t"] <= t]) <= 2
'''
NOTE_LIT = '''
def @FN@(sent, event, tz):
    return sum(1 for s in sent if event["t"] - s["t"] < 60) < 3
'''
NOTE_PAD = '''
def @FN@(sent, event, tz):
    t = event["t"]
    lm = (t + tz) % 1440
    if lm >= 1320 or lm < 420: return False
    if any(s["key"] == event["key"] and 0 <= t - s["t"] < 10 for s in sent): return False
    return sum(1 for s in sent if 0 <= t - s["t"] < 60) < 3
'''
NOTE_NULL = "def @FN@(sent, event, tz):\n    return False\n"

BUILD = {"meds": build_meds, "thermo": build_thermo, "split": build_split, "backup": build_backup, "pay": build_pay,
         "notify": build_notify}
# (ref, lit, pad, null, alt)
SRC = {"meds": (MEDS_REF, MEDS_LIT, MEDS_PAD, MEDS_NULL, MEDS_ALT),
       "thermo": (THERMO_REF, THERMO_LIT, THERMO_PAD, THERMO_NULL, THERMO_ALT),
       "split": (SPLIT_REF, SPLIT_LIT, SPLIT_PAD, SPLIT_NULL, SPLIT_ALT),
       "backup": (BACKUP_REF, BACKUP_LIT, BACKUP_PAD, BACKUP_NULL, BACKUP_ALT),
       "pay": (PAY_REF, PAY_LIT, PAY_PAD, PAY_NULL, PAY_ALT),
       "notify": (NOTE_REF, NOTE_LIT, NOTE_PAD, NOTE_NULL, NOTE_ALT)}
