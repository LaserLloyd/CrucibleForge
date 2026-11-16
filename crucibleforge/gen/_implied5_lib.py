"""IMPLIED level 5, brief `lib3`: library overdue fines. Three interacting functions (late_days, fine_cents, statement);
statement must compose the other two and decide the stop-at-return / renewal / grouping policy the brief never states.
3 explicit + 12 implied expectations. Scenarios are fixed at gen time; judging happens outside the sandbox.
implied.py pins this file's sha (_LIB_SHA) because chains.revision() digests only the family file."""
import random
from . import _implied_more as M

_L = M._L
RATE = {"book": 20, "dvd": 100}
CAPC = {"book": 1000, "dvd": 1500}

REF = '''
RATE = {"book": 20, "dvd": 100}
CAPC = {"book": 1000, "dvd": 1500}

def late_days(due_day, now, tz):
    return max(0, (now + tz) // 1440 - due_day)

def fine_cents(days, kind):
    return min(max(days, 0) * RATE[kind], CAPC[kind])

def statement(loans, returns, payments, now, tz):
    rets = {}
    for r in returns:
        rets.setdefault(r["item"], []).append(r["at"])
    by, names = {}, {}
    for ln in loans:
        due = max(ln["due_day"], ln.get("renewed_to", ln["due_day"]))
        back = [t for t in rets.get(ln["item"], []) if t >= ln["out"]]
        days = late_days(due, min(back) if back else now, tz)
        fine = fine_cents(days, ln["kind"])
        if fine > 0:
            m = ln["member"]
            names.setdefault(m, m)
            by.setdefault(m, []).append({"item": ln["item"], "late_days": days, "fine_cents": fine})
    paid = {}
    for p in payments:
        if not p.get("voided"):
            paid[p["member"]] = paid.get(p["member"], 0) + p["cents"]
    out = []
    for m, its in by.items():
        owed = sum(i["fine_cents"] for i in its) - paid.get(m, 0)
        if owed > 0:
            out.append({"member": names[m], "total_cents": owed, "items": its})
    return out
'''
LIT = '''
RATE = {"book": 20, "dvd": 100}
CAPC = {"book": 1000, "dvd": 1500}

def late_days(due_day, now, tz):
    return max(0, (now + tz) // 1440 - due_day)

def fine_cents(days, kind):
    return min(max(days, 0) * RATE[kind], CAPC[kind])

def statement(loans, returns, payments, now, tz):
    back = {r["item"] for r in returns}
    paid = {}
    for p in payments:
        paid[p["member"]] = paid.get(p["member"], 0) + p["cents"]
    by = {}
    for ln in loans:
        if ln["item"] in back:
            continue
        days = late_days(ln["due_day"], now, tz)
        fine = fine_cents(days, ln["kind"])
        if fine > 0:
            by.setdefault(ln["member"], []).append({"item": ln["item"], "late_days": days, "fine_cents": fine})
    out = []
    for m, its in by.items():
        owed = sum(i["fine_cents"] for i in its) - paid.get(m, 0)
        if owed > 0:
            out.append({"member": m, "total_cents": owed, "items": its})
    return out
'''
PAD = '''
RATE = {"book": 20, "dvd": 100}

def norm(x):
    return str(x).strip().lower()

def late_days(due_day, now, tz):
    return max(0, (now + tz) // 1440 - due_day)

def fine_cents(days, kind):
    return min(max(days, 0) * RATE[kind], 1000)

def statement(loans, returns, payments, now, tz):
    rets = {}
    for r in returns:
        rets.setdefault(norm(r["item"]), set()).add(r["at"])
    seen, by = set(), {}
    for ln in loans:
        key = (norm(ln["item"]), norm(ln["member"]), ln["out"])
        if key in seen or any(t >= ln["out"] for t in rets.get(norm(ln["item"]), ())):
            continue
        seen.add(key)
        due = max(ln["due_day"], ln.get("renewed_to", ln["due_day"]))
        days = late_days(due, now, tz)
        fine = fine_cents(days, ln["kind"])
        if fine >= 40:
            by.setdefault(norm(ln["member"]), []).append({"item": ln["item"], "late_days": days, "fine_cents": fine})
    paid = {}
    for p in payments:
        paid[norm(p["member"])] = paid.get(norm(p["member"]), 0) + p["cents"]
    out = []
    for m, its in by.items():
        owed = sum(i["fine_cents"] for i in its) - paid.get(m, 0)
        if owed > 0:
            out.append({"member": m, "total_cents": owed, "items": its})
    return out
'''
NULL = '''
def late_days(due_day, now, tz): return 0
def fine_cents(days, kind): return 0
def statement(loans, returns, payments, now, tz): return []
'''


def _c(f, *a):
    return {"f": f, "a": list(a)}


def _stmt(o):
    """-> {member: (total, {item: (late_days, fine)})} for a well-formed statement (one entry per member), else None"""
    if not isinstance(o, list):
        return None
    out = {}
    for e in o:
        if not isinstance(e, dict) or "member" not in e or not M._isint(e.get("total_cents")) or not isinstance(e.get("items"), list):
            return None
        its = {}
        for i in e["items"]:
            if not isinstance(i, dict) or "item" not in i or not M._isint(i.get("late_days")) or not M._isint(i.get("fine_cents")):
                return None
            its[str(i["item"]).strip().lower()] = (int(i["late_days"]), int(i["fine_cents"]))
        if len(its) != len(e["items"]):
            return None
        out[str(e["member"]).strip().lower()] = (int(e["total_cents"]), its)
    return out if len(out) == len(o) else None


def build(rng):
    tz = rng.choice([-480, -300, 60, 330, 540])
    d = rng.randint(14, 20)         # local day offset: the epoch day number of "today" is 7 + d
    D = lambda k: 7 + d + k         # epoch day number, k days from today
    ma, mb, mc = rng.sample(["M104", "M219", "M377", "M481", "M562", "M630"], 3)
    ia, ib, ic, idd = rng.sample(["B-1041", "B-2208", "B-3315", "B-4472", "B-5530", "B-6694", "B-7708"], 4)
    dv = rng.choice(["D-801", "D-822", "D-846"])
    S, J = {}, {}

    def add(i, calls, j, w, kind, why):
        S[i] = {"scen": {"calls": calls, "meta": {}}, "w": w, "kind": kind, "why": why}
        J[i] = j
    t = lambda k, h, m=0: _L(d + k, h, m, tz)       # a minute, k local days from today
    ln = lambda item, mem, kind, out, due, **kw: {"item": item, "member": mem, "kind": kind, "out": out, "due_day": due, **kw}
    ST = lambda loans, rets, now, pays=(): _c("stmt", loans, rets, list(pays), now, tz)
    only = lambda want: (lambda o, sc, p: (lambda s: s is not None and s == want)(_stmt(M._r0(o))))
    low = lambda x: x.lower()
    # ---- explicit
    add("x1", [_c("late", D(-2), t(0, 10), tz)], lambda o, sc, p: M._r0(o) == 2 and M._isint(M._r0(o)), .25, "x",
        "an item due two days ago is two days late")
    add("x2", [_c("fine", 3, "book")], lambda o, sc, p: M._r0(o) == 60 and M._isint(M._r0(o)), .25, "x",
        "three late days on a book cost 60 cents")
    add("x3", [ST([ln(ia, ma, "book", t(-20, 11), D(-5))], [], t(0, 10))],
        only({low(ma): (100, {low(ia): (5, 100)})}), .25, "x", "one unreturned overdue book gives its member one line")
    # ---- late_days / fine_cents
    add("notdue", [_c("late", D(0), t(0, 23, 59), tz), _c("late", D(3), t(0, 9), tz)],
        lambda o, sc, p: o == [0, 0], 1, "i", "the due day itself is not late (even at 23:59), and an item not yet due is 0 days late, not negative")
    add("capped", [_c("fine", 100, "book"), _c("fine", 100, "dvd")], lambda o, sc, p: o == [1000, 1500], 1, "a",
        "a fine never exceeds the item's replacement cost")
    # ---- statement
    add("latereturn", [ST([ln(ia, ma, "book", t(-30, 10), D(-12))], [{"item": ia, "at": t(-9, 14)}], t(0, 10))],
        only({low(ma): (60, {low(ia): (3, 60)})}), 1, "a",
        "an item that came back late still owes the fine up to the day it came back, and the fine stops growing after that")
    add("reloan", [ST([ln(ia, ma, "book", t(-40, 10), D(-26)), ln(ia, mb, "book", t(-10, 10), D(-4))],
                      [{"item": ia, "at": t(-28, 10)}], t(0, 10))],
        only({low(mb): (80, {low(ia): (4, 80)})}), 1, "i",
        "a return belongs to the loan that was out at the time: the later loan of the same item is still out and late")
    add("renew", [ST([ln(ia, ma, "book", t(-30, 10), D(-5), renewed_to=D(9))], [], t(0, 10))],
        only({}), 1, "i", "a renewed loan is due on the new day")
    add("group", [ST([ln(ia, ma, "book", t(-30, 10), D(-3)), ln(dv, ma, "dvd", t(-30, 10), D(-2)), ln(ib, mb, "book", t(-5, 10), D(9))],
                     [], t(0, 10))],
        only({low(ma): (260, {low(ia): (3, 60), low(dv): (2, 200)})}), 1, "a",
        "a member with two overdue items appears once, with both items and the sum of the fines; members who owe nothing are not listed")
    add("tzstmt", [ST([ln(ia, ma, "book", t(-30, 10), D(0))], [], t(0, 23, 30)),
                   ST([ln(ia, ma, "book", t(-30, 10), D(0))], [], t(1, 0, 30))],
        lambda o, sc, p: isinstance(o, list) and len(o) == 2 and _stmt(o[0]) == {} and _stmt(o[1]) == {low(ma): (20, {low(ia): (1, 20)})},
        1, "i", "the statement counts local days too: 23:30 on the due day owes nothing, 00:30 the next day owes one day")
    add("paid", [ST([ln(ia, ma, "book", t(-30, 10), D(-3)), ln(dv, ma, "dvd", t(-30, 10), D(-2)), ln(ib, mb, "dvd", t(-30, 10), D(-1))],
                    [], t(0, 10), [{"member": ma, "cents": 100}, {"member": mb, "cents": 100}])],
        only({low(ma): (160, {low(ia): (3, 60), low(dv): (2, 200)})}), 1, "a",
        "what a member has already paid comes off the total (not off the items); a member who has paid in full is not listed")
    add("voided", [ST([ln(ia, ma, "book", t(-30, 10), D(-3))], [], t(0, 10),
                       [{"member": ma, "cents": 60, "voided": True}, {"member": ma, "cents": 20}])],
        only({low(ma): (40, {low(ia): (3, 60)})}), 1, "i", "a payment that was voided never happened")
    text = ("Overdue fines for a small public library, in three parts.\n\n"
            "    def late_days(due_day, now, tz) -> int\n    def fine_cents(days, kind) -> int\n"
            "    def statement(loans, returns, payments, now, tz) -> list\n\n"
            "- Times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes); tz = the library's UTC offset "
            "in minutes (local time = UTC + tz). A local day is numbered floor((minute + tz) / 1440); due days use the same numbering.\n"
            "- late_days: how many local days late an item is at time `now`. An item is due by the end of its due day (due_day is a "
            "day number), so the due day is the last day it can come back without being late.\n"
            "- fine_cents: the fine in integer cents for `days` late days on an item of kind \"book\" (20 cents per day) or \"dvd\" "
            "(100 cents per day). A fine never exceeds the item's replacement cost: 1000 cents for a book, 1500 for a dvd.\n"
            "- statement: loans: [{\"item\": id, \"member\": id, \"kind\": \"book\" or \"dvd\", \"out\": minute, \"due_day\": day number, "
            "optionally \"renewed_to\": day number}] (renewed_to = the new due day if the loan was renewed); returns: "
            "[{\"item\": id, \"at\": minute}] (each time an item was scanned back in); payments: [{\"member\": id, \"cents\": int, "
            "optionally \"voided\": true}] (money handed in towards fines; voided = the payment was reversed). Return what each member still owes: "
            "[{\"member\": id, \"total_cents\": int, \"items\": [{\"item\": id, \"late_days\": int, \"fine_cents\": int}]}], one entry "
            "for every member who owes more than 0, in any order. Build it from the two functions above.")
    return {"text": text, "fn": {"late": "late_days", "fine": "fine_cents", "stmt": "statement"}, "S": S, "J": J}


def make(seed, wrap):
    b = build(random.Random(seed * 4813 + 41))
    exps = [{"id": i, "w": b["S"][i]["w"], "kind": b["S"][i]["kind"], "why": b["S"][i]["why"], "scen": b["S"][i]["scen"]}
            for i in b["S"]]
    return {"prompt": wrap + b["text"],
            "public": {"seed": seed, "tier": 5, "domain": "lib3", "fn": b["fn"], "params": {}, "n_exp": len(exps) - 3},
            "hidden": {"exps": exps}}


def judges(seed):
    return build(random.Random(seed * 4813 + 41))["J"]
