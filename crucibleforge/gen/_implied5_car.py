"""IMPLIED level 5, brief `car3`: shared-car booking for a small club. Three interacting functions (is_free, quote_cents,
assign); assign must compose the other two and decide the fairness / duplicate / past-request policy the brief never states.
3 explicit + 12 implied expectations. Scenarios are fixed at gen time; judging happens outside the sandbox.
implied.py pins this file's sha (_CAR_SHA) because chains.revision() digests only the family file."""
import random
from . import _implied_more as M

_L = M._L

REF = '''
def day_parts(start, end, tz):
    parts, t = [], start
    while t < end:
        day = (t + tz) // 1440
        seg = min(end, (day + 1) * 1440 - tz) - t
        parts.append((day, seg))
        t += seg
    return parts

def is_free(bookings, car, start, end):
    if end <= start:
        return False
    for b in bookings:
        if b.get("status") == "cancelled" or b["car"] != car:
            continue
        if b["start"] < end and start < b["end"]:
            return False
    return True

def quote_cents(start, end, tz, hourly_cents, day_cap_cents):
    return sum(min(-(-seg * hourly_cents // 60), day_cap_cents) for _, seg in day_parts(start, end, tz))

def assign(cars, bookings, requests, tz, hourly_cents, day_cap_cents):
    book = [dict(b) for b in bookings]
    res = {}
    for i in sorted(range(len(requests)), key=lambda i: (requests[i]["at"], i)):
        r = requests[i]
        car, price = None, 0
        if r["end"] > r["start"] and r["start"] >= r["at"]:
            mine = [b for b in book if b.get("status") != "cancelled" and b["member"] == r["member"]]
            if not any(b["start"] < r["end"] and r["start"] < b["end"] for b in mine):
                for c in cars:
                    if c["seats"] >= r["seats"] and is_free(book, c["id"], r["start"], r["end"]):
                        car = c["id"]
                        break
                if car:
                    prev = {}
                    for b in mine:
                        for day, seg in day_parts(b["start"], b["end"], tz):
                            prev[day] = prev.get(day, 0) + -(-seg * hourly_cents // 60)
                    for day, seg in day_parts(r["start"], r["end"], tz):
                        p = prev.get(day, 0)
                        price += min(day_cap_cents, p + -(-seg * hourly_cents // 60)) - min(day_cap_cents, p)
                    book.append({"car": car, "member": r["member"], "start": r["start"], "end": r["end"], "status": "confirmed"})
        res[i] = {"request": r["id"], "car": car, "price_cents": price}
    return [res[i] for i in range(len(requests))]
'''
LIT = '''
def is_free(bookings, car, start, end):
    return not any(b["car"] == car and b["start"] < end and start < b["end"] for b in bookings)

def quote_cents(start, end, tz, hourly_cents, day_cap_cents):
    return -(-(end - start) * hourly_cents // 60)

def assign(cars, bookings, requests, tz, hourly_cents, day_cap_cents):
    book = [dict(b) for b in bookings]
    out = []
    for r in requests:
        car = None
        for c in cars:
            if c["seats"] >= r["seats"] and is_free(book, c["id"], r["start"], r["end"]):
                car = c["id"]
                book.append({"car": car, "member": r["member"], "start": r["start"], "end": r["end"], "status": "confirmed"})
                break
        out.append({"request": r["id"], "car": car,
                    "price_cents": quote_cents(r["start"], r["end"], tz, hourly_cents, day_cap_cents) if car else 0})
    return out
'''
PAD = '''
def is_free(bookings, car, start, end):
    if end <= start:
        return False
    for b in bookings:
        if b["car"] != car:
            continue
        if b["start"] - 15 < end and start < b["end"] + 15:
            return False
    return True

def quote_cents(start, end, tz, hourly_cents, day_cap_cents):
    if end <= start:
        return 0
    hours = -(-(end - start) // 60)
    return min(hours * hourly_cents, day_cap_cents)

def assign(cars, bookings, requests, tz, hourly_cents, day_cap_cents):
    book = [dict(b) for b in bookings]
    held = {}
    res = {}
    for i in sorted(range(len(requests)), key=lambda i: (requests[i]["at"], i)):
        r = requests[i]
        car = None
        mine = held.setdefault(r["member"], [])
        if (r["end"] > r["start"] and r["start"] >= r["at"] and r["end"] - r["start"] <= 720
                and not any(s < r["end"] and r["start"] < e for s, e in mine)):
            for c in sorted(cars, key=lambda c: (c["seats"], c["id"])):
                if c["seats"] >= r["seats"] and is_free(book, c["id"], r["start"], r["end"]):
                    car = c["id"]
                    book.append({"car": car, "member": r["member"], "start": r["start"], "end": r["end"], "status": "confirmed"})
                    mine.append((r["start"], r["end"]))
                    break
        price = quote_cents(r["start"], r["end"], tz, hourly_cents, day_cap_cents) if car else 0
        res[i] = {"request": r["id"], "car": car, "price_cents": price}
    return [res[i] for i in range(len(requests))]
'''
NULL = '''
def is_free(bookings, car, start, end): return False
def quote_cents(start, end, tz, hourly_cents, day_cap_cents): return 0
def assign(cars, bookings, requests, tz, hourly_cents, day_cap_cents):
    return [{"request": r["id"], "car": None, "price_cents": 0} for r in requests]
'''


def _c(f, *a):
    return {"f": f, "a": list(a)}


def _quote(start, end, tz, h, cap):
    """judge-side reference of quote_cents"""
    total, t = 0, start
    while t < end:
        day_end = ((t + tz) // 1440 + 1) * 1440 - tz
        seg = min(end, day_end) - t
        total += min(-(-seg * h // 60), cap)
        t += seg
    return total


def _asg(o):
    """-> {request id: (car, price)} for a well-formed assign() result, else None"""
    if not isinstance(o, list):
        return None
    out = {}
    for e in o:
        if not isinstance(e, dict) or "request" not in e or "car" not in e or not M._num(e.get("price_cents")):
            return None
        out[e["request"]] = (e["car"], e["price_cents"])
    return out if len(out) == len(o) else None


def build(rng):
    tz = rng.choice([-480, -300, 60, 330, 540])
    H = rng.choice([1250, 1750, 1999, 2150])
    CAP = 6 * H
    d = rng.randint(1, 3)
    car1, car2, car3 = rng.sample(["Mini", "Kombi", "Coupe", "Wagon", "Panda", "Estate"], 3)
    ma, mb = rng.sample(["Ana", "Ben", "Chiara", "Dev", "Elif", "Farid"], 2)
    S, J = {}, {}

    def add(i, calls, j, w, kind, why):
        S[i] = {"scen": {"calls": calls, "meta": {}}, "w": w, "kind": kind, "why": why}
        J[i] = j
    q = lambda s, e: _quote(s, e, tz, H, CAP)
    ok1 = lambda v: (lambda o, sc, p: M._r0(o) is v)
    day = lambda k, h, m=0: _L(d + k, h, m, tz)
    bk = lambda car, s, e, st="confirmed", member="Gus": {"car": car, "member": member, "start": s, "end": e, "status": st}
    cars2 = [{"id": car1, "seats": 4}, {"id": car2, "seats": 4}]
    rq = lambda i, mem, seats, s, e, at: {"id": i, "member": mem, "seats": seats, "start": s, "end": e, "at": at}
    AS = lambda cars, books, reqs: _c("assign", cars, books, reqs, tz, H, CAP)

    # ---- explicit
    add("x1", [_c("free", [bk(car1, day(1, 9), day(1, 12))], car1, day(1, 15), day(1, 17)),
               _c("free", [bk(car1, day(1, 9), day(1, 12))], car1, day(1, 10), day(1, 11))],
        lambda o, sc, p: isinstance(o, list) and len(o) == 2 and o[0] is True and o[1] is False, .25, "x",
        "a free window is free and a window inside a booking is not")
    add("x2", [_c("quote", day(1, 10), day(1, 12), tz, H, CAP)],
        lambda o, sc, p: M._r0(o) == 2 * H and M._isint(M._r0(o)), .25, "x", "two hours cost two hourly rates")
    add("x3", [AS([{"id": car1, "seats": 4}], [], [rq("r1", ma, 2, day(1, 10), day(1, 12), day(0, 9))])],
        lambda o, sc, p: _asg(M._r0(o)) == {"r1": (car1, 2 * H)}, .25, "x", "one request, one free car: it gets the car at the quoted price")
    # ---- is_free
    add("cancel", [_c("free", [bk(car1, day(1, 9), day(1, 12), "cancelled")], car1, day(1, 10), day(1, 11))],
        ok1(True), 1, "i", "a cancelled booking no longer blocks the car")
    add("adjacent", [_c("free", [bk(car1, day(1, 9), day(1, 12))], car1, day(1, 12), day(1, 14)),
                     _c("free", [bk(car1, day(1, 9), day(1, 12))], car1, day(1, 7), day(1, 9))],
        lambda o, sc, p: isinstance(o, list) and o == [True, True], 1, "i",
        "back-to-back bookings are fine: ending when another starts is not an overlap")
    # ---- quote_cents
    s2, e2 = day(1, 17), day(2, 5)
    add("tzsplit", [_c("quote", s2, e2, tz, H, CAP)], lambda o, sc, p: M._r0(o) == q(s2, e2), 1, "i",
        "a booking over local midnight is two local days, each capped on its own (local midnight, not UTC midnight)")
    s3, e3 = day(1, 18), day(3, 6)
    add("multiday", [_c("quote", s3, e3, tz, H, CAP)], lambda o, sc, p: M._r0(o) == q(s3, e3), 1, "i",
        "a day and a half over three local days: each day is capped on its own, then they add up")
    m = next(m for m in range(1, 60) if 1 <= (m * H) % 60 <= 29)
    s4 = day(1, 11)
    add("cent", [_c("quote", s4, s4 + m, tz, H, CAP)], lambda o, sc, p: M._r0(o) == q(s4, s4 + m), 1, "a",
        "a part cent is rounded up, never down")
    # ---- assign
    w1 = (day(1, 14), day(1, 16))
    add("fcfs", [AS([{"id": car1, "seats": 4}], [],
                    [rq("late", mb, 2, *w1, day(0, 9, 30)), rq("early", ma, 2, *w1, day(0, 9, 0))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("early", (0,))[0] == car1 and a.get("late", (0,))[0] is None)(_asg(M._r0(o))),
        1, "a", "the request submitted first gets the only car, whatever its place in the list")
    add("fallback", [AS([{"id": car1, "seats": 4}, {"id": car2, "seats": 4}], [bk(car1, day(1, 13), day(1, 17))],
                        [rq("r1", ma, 2, *w1, day(0, 9))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1") == (car2, q(*w1)))(_asg(M._r0(o))), 1, "i",
        "when the first car is busy the request goes to another free car")
    add("onecar", [AS(cars2, [], [rq("r1", ma, 2, *w1, day(0, 9)), rq("r2", ma, 2, *w1, day(0, 9, 1))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1", (None,))[0] in (car1, car2) and a.get("r2", (0,))[0] is None
                          and a["r2"][1] == 0)(_asg(M._r0(o))), 1, "a",
        "the same member asking twice for the same time (a double tap) gets one car, not two")
    add("onecarex", [AS(cars2, [bk(car1, w1[0] - 30, w1[1] - 30, member=ma)], [rq("r1", ma, 2, *w1, day(0, 9))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1") == (None, 0))(_asg(M._r0(o))), 1, "a",
        "a member who already holds a car for that time is not given a second one for it")
    add("past", [AS(cars2, [], [rq("r1", ma, 2, day(0, 11), day(0, 13), day(0, 12))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1") == (None, 0))(_asg(M._r0(o))),
        1, "i", "a booking that starts before the moment it was asked for is refused")
    add("seats", [AS([{"id": car1, "seats": 2}, {"id": car3, "seats": 7}], [],
                     [rq("r1", ma, 5, *w1, day(0, 9)), rq("r2", mb, 9, w1[0], w1[1] + 120, day(0, 9, 5))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1") == (car3, q(*w1)) and a.get("r2") == (None, 0))(_asg(M._r0(o))),
        1, "i", "a car with too few seats is never given; a party that fits no car gets none")
    add("sharedcap", [AS(cars2, [bk(car2, day(1, 8), day(1, 12), member=ma)], [rq("r1", ma, 2, day(1, 13), day(1, 17), day(0, 9))])],
        lambda o, sc, p: (lambda a: a is not None and a.get("r1", (0,))[0] in (car1, car2)
                          and a["r1"][1] == min(CAP, 8 * H) - 4 * H)(_asg(M._r0(o))),
        1, "a", "a member never pays more than the daily cap in total: eight hours in one day, four of them already booked, cost the cap in all")
    text = ("Shared-car booking for a small neighbourhood club, in three parts.\n\n"
            "    def is_free(bookings, car, start, end) -> bool\n"
            "    def quote_cents(start, end, tz, hourly_cents, day_cap_cents) -> int\n"
            "    def assign(cars, bookings, requests, tz, hourly_cents, day_cap_cents) -> list\n\n"
            "- All times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes); tz = the club's UTC "
            "offset in minutes (local time = UTC + tz). A booking covers its start up to, but not including, its end.\n"
            "- is_free: bookings: [{\"car\": id, \"member\": str, \"start\": minute, \"end\": minute, \"status\": \"confirmed\" or \"cancelled\"}]. "
            "Return True if `car` can be booked for the whole of [start, end).\n"
            "- quote_cents: the price in integer cents of a booking from start to end. It is charged at hourly_cents per hour, by the "
            "minute, separately for every local calendar day (local midnight to local midnight) the booking touches; each day's charge "
            "is rounded up to a whole cent and is at most day_cap_cents (the most one member pays for one local day). Return the sum over those days.\n"
            "- assign: cars: [{\"id\": str, \"seats\": int}]; bookings: as above; requests: [{\"id\": str, \"member\": str, "
            "\"seats\": int, \"start\": minute, \"end\": minute, \"at\": minute}] (at = when the member submitted the request). "
            "Return one entry per request, in the same order as requests: {\"request\": id, \"car\": car id or None, "
            "\"price_cents\": int}; price_cents is what the member is charged for this request, 0 when car is None. Build it from the two "
            "functions above: a request that gets a car becomes a confirmed booking.")
    return {"text": text, "fn": {"free": "is_free", "quote": "quote_cents", "assign": "assign"}, "S": S, "J": J}



#: checks the brief contradicts or never implies (scrub review, 2026-11-14): not scored; kept in build() so the rng draws
#: (and every other check's scenario) stay byte-identical. onecar / onecarex rest on a one-car-per-member rule the brief never states (two requests with their own ids each
#: get a car; the brief asks for one entry per request)
DROPPED = {"onecar", "onecarex"}

def make(seed, wrap):
    b = build(random.Random(seed * 6271 + 29))
    exps = [{"id": i, "w": b["S"][i]["w"], "kind": b["S"][i]["kind"], "why": b["S"][i]["why"], "scen": b["S"][i]["scen"]}
            for i in b["S"] if i not in DROPPED]
    return {"prompt": wrap + b["text"],
            "public": {"seed": seed, "tier": 5, "domain": "car3", "fn": b["fn"], "params": {}, "n_exp": len(exps) - 3},
            "hidden": {"exps": exps}}


def judges(seed):
    return build(random.Random(seed * 6271 + 29))["J"]
