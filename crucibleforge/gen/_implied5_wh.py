"""IMPLIED level 5, brief `wh3`: small warehouse pick-pack-ship planner. Three interacting functions (free_stock, ship_day,
plan_picks); plan_picks must compose the other two and decide fairness / reservation / duplicate policy the brief never states
(the trap: free_stock already subtracts the open orders, so planning on it reserves every order against itself).
3 explicit + 12 implied expectations. Scenarios are fixed at gen time; judging happens outside the sandbox.
implied.py pins this file's sha (_WH_SHA) because chains.revision() digests only the family file."""
import random
from . import _implied_more as M

_L = M._L

REF = '''
def norm(x):
    return str(x).strip().lower()

def current(orders):
    seen, out = set(), []
    for o in orders:
        if o["id"] not in seen:
            seen.add(o["id"])
            out.append(o)
    return out

def free_stock(stock, orders):
    free = dict(stock)
    key = {norm(k): k for k in stock}
    for o in current(orders):
        if o["status"] in ("open", "packed"):
            for k, q in o["lines"].items():
                if norm(k) in key:
                    free[key[norm(k)]] -= q
    return {k: max(v, 0) for k, v in free.items()}

def ship_day(placed, tz, cutoff):
    day = (placed + tz) // 1440
    if (placed + tz) % 1440 >= cutoff:
        day += 1
    while day % 7 > 4:
        day += 1
    return day

def plan_picks(stock, orders, now, tz, cutoff):
    today = (now + tz) // 1440
    free = {norm(k): v for k, v in stock.items()}
    todo = []
    for o in current(orders):
        lines = {}
        for k, q in o["lines"].items():
            lines[norm(k)] = lines.get(norm(k), 0) + q
        if o["status"] == "packed":
            for k, q in lines.items():
                if k in free:
                    free[k] -= q
        elif o["status"] == "open" and lines and o["placed"] <= now and ship_day(o["placed"], tz, cutoff) <= today:
            todo.append((o, lines))
    out = []
    for o, lines in sorted(todo, key=lambda x: x[0]["placed"]):
        if all(q <= free.get(k, 0) for k, q in lines.items()):
            for k, q in lines.items():
                free[k] -= q
            out.append(o["id"])
    return out
'''
LIT = '''
def free_stock(stock, orders):
    free = dict(stock)
    for o in orders:
        if o["status"] != "shipped":
            for k, q in o["lines"].items():
                if k in free:
                    free[k] -= q
    return free

def ship_day(placed, tz, cutoff):
    day = (placed + tz) // 1440
    if (placed + tz) % 1440 >= cutoff:
        day += 1
    while day % 7 > 4:
        day += 1
    return day

def plan_picks(stock, orders, now, tz, cutoff):
    free = free_stock(stock, orders)
    out = []
    for o in orders:
        if o["status"] == "open" and all(free.get(k, 0) >= 0 for k in o["lines"]):
            out.append(o["id"])
    return out
'''
PAD = '''
def free_stock(stock, orders):
    free = dict(stock)
    for o in orders:
        if o["status"] in ("open", "packed"):
            for k, q in o["lines"].items():
                if k in free:
                    free[k] -= q
    return {k: max(v, 0) for k, v in free.items()}

def ship_day(placed, tz, cutoff):
    day = (placed + tz) // 1440
    if (placed + tz) % 1440 > cutoff:
        day += 1
    while day % 7 > 4:
        day += 1
    return day

def plan_picks(stock, orders, now, tz, cutoff):
    today = (now + tz) // 1440
    free = dict(stock)
    seen = set()
    todo = []
    for o in orders:
        if o["id"] in seen:
            continue
        seen.add(o["id"])
        if o["status"] == "packed":
            for k, q in o["lines"].items():
                if k in free:
                    free[k] -= q
        elif o["status"] == "open" and ship_day(o["placed"], tz, cutoff) <= today:
            todo.append(o)
    out = []
    for o in sorted(todo, key=lambda o: o["placed"]):
        if all(q <= free.get(k, 0) for k, q in o["lines"].items()):
            for k, q in o["lines"].items():
                free[k] -= q
            out.append(o["id"])
    return out[:2]
'''
NULL = '''
def free_stock(stock, orders): return dict(stock)
def ship_day(placed, tz, cutoff): return 0
def plan_picks(stock, orders, now, tz, cutoff): return []
'''


def _c(f, *a):
    return {"f": f, "a": list(a)}


def _stock(o, want):
    return isinstance(o, dict) and set(o) == set(want) and all(M._isint(v) and int(v) == want[k] for k, v in o.items())


def build(rng):
    tz = rng.choice([-480, -300, 60, 330, 540])
    cutoff = rng.choice([840, 900, 960])
    mon = 7 * rng.randint(0, 2)             # d = mon + weekday; the epoch day number of weekday w is 7 + mon + w
    A, B, C = rng.sample(["bolt", "washer", "hinge", "bracket", "clamp", "rivet", "gasket"], 3)
    ids = rng.sample(range(1001, 1999), 8)
    O = [f"W-{i}" for i in ids]
    S, J = {}, {}

    def add(i, calls, j, w, kind, why):
        S[i] = {"scen": {"calls": calls, "meta": {}}, "w": w, "kind": kind, "why": why}
        J[i] = j
    t = lambda wd, h, m=0: _L(mon + wd, h, m, tz)         # local weekday wd (0 = Monday), h:m
    DN = lambda wd: 7 + mon + wd                          # the day number of that weekday
    od = lambda i, st, placed, **lines: {"id": i, "status": st, "placed": placed, "lines": dict(lines)}
    PL = lambda stock, orders, wd, h: _c("plan", stock, orders, t(wd, h), tz, cutoff)
    pl = lambda want: (lambda o, sc, p: M._r0(o) == want)
    st = lambda want: (lambda o, sc, p: _stock(M._r0(o), want))
    # ---- explicit
    add("x1", [_c("free", {A: 10, B: 4}, [od(O[0], "open", t(1, 9), **{A: 3})])], st({A: 7, B: 4}), .25, "x",
        "an open order takes its units out of what can be promised")
    add("x2", [_c("ship", t(1, 10), tz, cutoff)], lambda o, sc, p: M._r0(o) == DN(1) and M._isint(M._r0(o)), .25, "x",
        "an order placed before the cut-off on a weekday ships that day")
    add("x3", [PL({A: 5}, [od(O[0], "open", t(1, 9), **{A: 2})], 1, 16)], pl([O[0]]), .25, "x",
        "one open order with enough stock is picked")
    # ---- free_stock
    add("multi", [_c("free", {A: 10, B: 5, C: 2},
                     [od(O[0], "open", t(0, 9), **{A: 2, B: 1}), od(O[1], "open", t(0, 10), **{A: 3}),
                      od(O[2], "packed", t(0, 11), **{B: 2}), od(O[3], "shipped", t(0, 8), **{A: 9}),
                      od(O[4], "open", t(0, 12), **{C: 3}), od(O[5], "cancelled", t(0, 13), **{A: 4, B: 2})])],
        st({A: 5, B: 2, C: 0}), 1, "i",
        "every open and packed order counts, on every sku; shipped and cancelled orders hold nothing; you cannot promise fewer than none")
    add("dupfree", [_c("free", {A: 10, B: 4}, [od(O[0], "open", t(0, 9), **{A: 3}), od(O[0], "open", t(0, 9), **{A: 3}),
                                                od(O[1], "packed", t(0, 10), **{B: 1})])],
        st({A: 7, B: 3}), 1, "i", "the same order listed twice (a double submit) holds its stock once")
    # ---- ship_day
    add("tzcut", [_c("ship", t(2, cutoff // 60 - 1, 30 + (cutoff % 60)), tz, cutoff), _c("ship", t(2, cutoff // 60, cutoff % 60), tz, cutoff),
                  _c("ship", t(4, 18), tz, cutoff)],
        lambda o, sc, p: o == [DN(2), DN(3), DN(7)], 1, "a",
        "the cut-off is the warehouse's local clock, an order placed exactly at the cut-off has missed it, and Friday evening ships Monday")
    # ---- plan_picks
    add("fifo", [PL({A: 20}, [od(O[2], "open", t(1, 11), **{A: 2}), od(O[0], "open", t(0, 9), **{A: 2}),
                              od(O[1], "open", t(1, 10), **{A: 2})], 1, 16),
                 PL({A: 3}, [od(O[1], "open", t(1, 11), **{A: 3}), od(O[0], "open", t(1, 9), **{A: 3})], 1, 16)],
        lambda o, sc, p: o == [[O[0], O[1], O[2]], [O[0]]], 1, "a",
        "orders are picked oldest first, whatever order the list comes in, and when stock runs out the order that waited longest gets it")
    add("notyet", [PL({A: 20}, [od(O[0], "open", t(0, 17), **{A: 1}), od(O[1], "open", t(1, 17), **{A: 1}),
                                od(O[2], "open", t(1, 10), **{A: 1})], 1, 18)],
        pl([O[0], O[2]]), 1, "a", "an order that has not reached its shipping day is not picked yet (the Tuesday-evening order waits for Wednesday)")
    add("packedres", [PL({A: 5}, [od(O[3], "packed", t(0, 9), **{A: 3}), od(O[0], "open", t(1, 9), **{A: 2}),
                                  od(O[1], "open", t(1, 10), **{A: 1})], 1, 16)],
        pl([O[0]]), 1, "i", "units already packed for another order are not available to pick again")
    add("selfres", [PL({A: 4, B: 2}, [od(O[0], "open", t(1, 9), **{A: 4, B: 2})], 1, 16)],
        pl([O[0]]), 1, "a", "an order that uses up exactly the stock is still picked: it must not be blocked by its own reservation")
    add("allornone", [PL({A: 2, B: 1}, [od(O[0], "open", t(1, 9), **{A: 2, B: 5}), od(O[1], "open", t(1, 10), **{A: 2})], 1, 16)],
        pl([O[1]]), 1, "a", "an order that cannot be filled in full keeps nothing back from the next one")
    add("dupid", [PL({A: 4}, [od(O[0], "open", t(1, 9), **{A: 2}), od(O[0], "open", t(1, 9), **{A: 2}),
                              od(O[1], "open", t(1, 10), **{A: 2})], 1, 16)],
        pl([O[0], O[1]]), 1, "i", "the same order listed twice (a double submit) is one order: picked once, and its stock held once")
    add("bulk", [PL({A: 20}, [od(O[i], "open", t(1, 8 + i % 4), **{A: 1}) for i in range(4)], 1, 16)],
        pl([O[0], O[1], O[2], O[3]]), 1, "a", "there is no limit on how many orders one planning call may release")
    add("empty", [PL({A: 5}, [od(O[0], "open", t(1, 9)), od(O[1], "open", t(1, 10), **{A: 2})], 1, 16)],
        pl([O[1]]), 1, "i", "an order with nothing left on it has nothing to pick")
    add("future", [PL({A: 5}, [od(O[0], "open", t(1, 9), **{A: 2}), od(O[1], "open", t(1, 7), **{A: 2})], 1, 8)],
        pl([O[1]]), 1, "i", "an order that is stamped later than the moment of planning does not exist yet")
    text = ("Pick-pack-ship planner for a small warehouse, in three parts.\n\n"
            "    def free_stock(stock, orders) -> dict\n    def ship_day(placed, tz, cutoff) -> int\n"
            "    def plan_picks(stock, orders, now, tz, cutoff) -> list\n\n"
            "- Times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes); tz = the warehouse's UTC "
            "offset in minutes (local time = UTC + tz). A local day is numbered floor((minute + tz) / 1440); its weekday is that number "
            "mod 7 (0 = Monday ... 4 = Friday, 5 = Saturday, 6 = Sunday).\n"
            "- stock: {sku: units physically in the warehouse now}. orders: [{\"id\": str, \"status\": \"open\" or \"packed\" or "
            "\"shipped\" or \"cancelled\", \"placed\": minute, \"lines\": {sku: units}}]. Packed orders are still in the warehouse "
            "(they are part of stock); shipped orders have left and are not.\n"
            "- free_stock: return {sku: units that can still be promised to new orders} for every sku in stock (for example, 10 nuts in "
            "stock and one open order for 3 nuts leave {\"nut\": 7}).\n"
            "- ship_day: the day number on which an order placed at minute `placed` ships. The warehouse ships only on weekdays "
            "(Monday to Friday); `cutoff` is in minutes after local midnight: an order placed before the cut-off can ship the same "
            "day, one placed at or after it ships the next day, and a day that is not a shipping day moves on to the next one.\n"
            "- plan_picks: the warehouse calls this to decide what the pickers should pull at time `now`. Return the ids of the orders "
            "to pick now, in the order they should be picked. An order is picked in full or not at all (every line must be covered "
            "by stock). Build it from the two functions above.")
    return {"text": text, "fn": {"free": "free_stock", "ship": "ship_day", "plan": "plan_picks"}, "S": S, "J": J}


#: implied checks no more than 1 of 3 independent Opus readers listed (F-055 panel): not scored; kept in build() so the
#: rng draws (and every other check's scenario) stay byte-identical
DROPPED = {"dupfree", "dupid", "empty", "future"}


def make(seed, wrap):
    b = build(random.Random(seed * 3571 + 53))
    exps = [{"id": i, "w": b["S"][i]["w"], "kind": b["S"][i]["kind"], "why": b["S"][i]["why"], "scen": b["S"][i]["scen"]}
            for i in b["S"] if i not in DROPPED]
    return {"prompt": wrap + b["text"],
            "public": {"seed": seed, "tier": 5, "domain": "wh3", "fn": b["fn"], "params": {}, "n_exp": len(exps) - 3},
            "hidden": {"exps": exps}}


def judges(seed):
    return build(random.Random(seed * 3571 + 53))["J"]
