"""IMPLIED domain bank, part 3: everyday domains for tier 1 (mileage claims, invoice due dates, shipping cost,
grade average, event RSVP, leaderboard, shop returns, loyalty points, expense payout). Same contract as _implied_more builders: build_X(rng, tier) -> {text, fn, S, judges,
params, FORCE, POOL}.

Every domain has THREE brief texts (variant ``v`` = 0, 1, 2) that differ in the stated rule, not only in names; the rule is
always in the brief, so the checks never depend on a hidden representation choice (F-042). Implied checks are the things a
sensible person expects and the brief leaves unsaid (duplicates, empty/missing values, boundaries, distinct things that look
alike). The FORCE checks are the anti-pad checks: a distinct-looking-alike case that a blanket "dedupe everything" safeguard
gets wrong (a round trip, two invoices of one customer, the same SKU on two cart lines, two quizzes
with equal marks, two RSVPs alike, tied players).
Solutions carry the variant's parameters as ``PRM = @PARAMS@`` (substituted from the instance's public params)."""
import math

from ._implied_more import _B, _call, _isint, _out, _r0


def _half_up_avg(xs):
    """Average of whole numbers, rounded to the nearest whole number, halves up (toward +infinity: -20.5 -> -20,
    20.5 -> 21). Exact integer arithmetic: floor(s/n + 1/2) = (2s + n) // 2n. int(x + 0.5) truncates toward zero and
    is one too high for every negative non-half average (the 2026-11-14 board fault)."""
    s, n = sum(xs), len(xs)
    return (2 * s + n) // (2 * n)


def _int_is(o, v):
    r = _r0(o)
    return _isint(r) and int(r) == v


# ======================================================= MILEAGE CLAIMS
def build_mileage(rng, tier):
    fn = rng.choice(["claim_cents", "mileage_claim", "total_claim"])
    v = rng.randrange(4)
    R = rng.choice([45, 62, 70])
    D = rng.choice([80, 100, 120])
    T = rng.choice([30, 50])
    R2 = R - 15
    prm = {"v": v, "R": R, "D": D, "T": T, "R2": R2}
    d0 = rng.randint(100, 300)
    b = _B()

    def cost(km):        # one day's total km
        if v == 1:
            return R * min(km, D)
        if v == 2:
            return R * min(km, T) + R2 * max(0, km - T)
        return R * km

    def ek(km):          # what one trip counts for (v3: rounded up to whole 5 km)
        return -(-km // 5) * 5 if v == 3 else km

    def cost_t(*kms):    # one day's trips
        return cost(sum(ek(k) for k in kms))

    def Tr(i, day, km):
        return {"id": f"t{i}", "day": day, "km": km}

    def S(trips):
        return [_call(trips)]
    b.add("x1", S([Tr(1, d0, 20)]), lambda o, sc, p: _int_is(o, cost_t(20)), .25, "x", "a trip is paid by the km")
    b.add("x2", S([Tr(1, d0, 12), Tr(2, d0 + 1, 18)]), lambda o, sc, p: _int_is(o, cost_t(12) + cost_t(18)), .25, "x",
          "trips on different days add up")
    if v == 0:
        b.add("x3", S([Tr(1, d0, 5), Tr(2, d0, 7), Tr(3, d0, 9)]), lambda o, sc, p: _int_is(o, cost(21)), .25, "x",
              "several trips on one day add up")
    elif v == 3:
        b.add("x3", S([Tr(1, d0, 7), Tr(2, d0, 7), Tr(3, d0, 7)]), lambda o, sc, p: _int_is(o, cost_t(7, 7, 7)), .25, "x",
              "each trip is rounded up to 5 km before it is paid: three 7 km trips are 30 km")
    elif v == 1:
        b.add("x3", S([Tr(1, d0, D // 2 + 10), Tr(2, d0, D // 2 + 10)]), lambda o, sc, p: _int_is(o, cost(D + 20)), .25, "x",
              "a day's claim stops at the daily limit")
    else:
        b.add("x3", S([Tr(1, d0, T - 10), Tr(2, d0, 25)]), lambda o, sc, p: _int_is(o, cost(T + 15)), .25, "x",
              "the second rate starts after the first T km of the day")
    b.add("nonpos", S([Tr(1, d0, 18), Tr(2, d0 + 1, 0), Tr(3, d0 + 2, -7)]), lambda o, sc, p: _int_is(o, cost_t(18)), 1, "i",
          "a zero or negative distance never reduces the claim")
    b.add("unknown", S([Tr(1, d0, 25), Tr(2, d0 + 1, None)]), lambda o, sc, p: _int_is(o, cost_t(25)), 1, "i",
          "a trip with no recorded distance is left out, the rest is still paid")
    a_, c_ = (D // 2 + 15, D // 2 + 15) if v == 1 else ((T - 10, 25) if v == 2 else (10, 20))
    b.add("splitday", S([Tr(1, d0, a_), Tr(2, d0 + 1, 10), Tr(3, d0, c_)]), lambda o, sc, p: _int_is(o, cost_t(a_, c_) + cost_t(10)),
          1, "i", "trips of one day count together even when other days' trips sit between them")
    b.add("roundtrip", S([Tr(1, d0, 14), Tr(2, d0, 14)]), lambda o, sc, p: _int_is(o, cost_t(14, 14)), 1, "a",
          "to work and back the same day is two trips of the same length, both paid")
    rule = [f"Each km claimed pays {R} cents.",
            f"Each km pays {R} cents, but at most {D} km can be claimed for one day (all trips of that day together).",
            f"On each day the first {T} km (all trips of that day together) pay {R} cents per km and every further km "
            f"that day pays {R2} cents.",
            f"Each trip is first rounded up to a whole 5 km (a 12 km trip counts as 15), then each km pays {R} cents."][v]
    text = (f"Mileage claim for one employee.\n\n    def {fn}(trips) -> int\n\n"
            "- trips: [{\"id\": str, \"day\": whole day number, \"km\": whole km, or None if no distance was recorded}].\n"
            f"- {rule}\n- Return the total claim in integer cents.")
    return _out(b, text, fn, ["roundtrip"], ["nonpos", "unknown", "splitday"], prm)


MILE_HEAD = '''
PRM = @PARAMS@
def _e(km):
    return -(-km // 5) * 5 if PRM["v"] == 3 else km
def _c(km):
    if PRM["v"] == 1: return PRM["R"] * min(km, PRM["D"])
    if PRM["v"] == 2: return PRM["R"] * min(km, PRM["T"]) + PRM["R2"] * max(0, km - PRM["T"])
    return PRM["R"] * km
'''
MILE_REF = MILE_HEAD + '''
def @FN@(trips):
    seen = {}
    for t in trips:
        seen[t["id"]] = t
    days = {}
    for t in seen.values():
        k = t["km"]
        if isinstance(k, (int, float)) and k > 0:
            days[t["day"]] = days.get(t["day"], 0) + _e(k)
    return sum(_c(k) for k in days.values())
'''
MILE_ALT = MILE_HEAD + '''
def @FN@(trips):
    by = {}
    for t in sorted(trips, key=lambda t: (t["day"], str(t["id"]))):
        if t.get("km") and t["km"] > 0:
            by.setdefault(t["day"], {})[t["id"]] = _e(t["km"])
    return int(sum(_c(sum(g.values())) for g in by.values()))
'''
MILE_LIT = MILE_HEAD + '''
def @FN@(trips):
    days = {}
    for t in trips:
        days[t["day"]] = days.get(t["day"], 0) + _e(t["km"])
    return sum(_c(k) for k in days.values())
'''
MILE_PAD = MILE_REF.replace('seen[t["id"]] = t', 'seen.setdefault((t["day"], t["km"]), t)')
MILE_NULL = "def @FN@(trips):\n    return 0\n"


# ======================================================= INVOICE DUE DATES
def build_invoices(rng, tier):
    fn = rng.choice(["overdue_ids", "overdue_invoices", "unpaid_overdue"])
    v = rng.randrange(4)
    prm = {"v": v}
    d0 = rng.randint(100, 300)
    b = _B()
    cust = rng.sample(["Acme", "Birch", "Cobalt", "Delta", "Ember", "Fjord"], 3)
    TERMS = {"net15": 15, "net30": 30, "net60": 60}

    def I(i, issued, days, amount, paid, c=None):
        t = f"net{days}" if v == 2 else days
        pay = (sum(paid) if v == 1 else list(paid))
        return {"id": f"inv{i}", "customer": c or cust[i % 3], "issued": issued, "terms": t, "amount": amount, "paid": pay}

    def S(invs, today):
        return [_call(invs, today)]

    def ids(o, want):
        r = _r0(o)
        return isinstance(r, list) and [str(x) for x in r] == want
    b.add("x1", S([I(2, d0, 30, 50000, []), I(1, d0 - 20, 30, 12000, [])], d0 + 45),
          lambda o, sc, p: ids(o, ["inv1", "inv2"]), .25, "x", "unpaid invoices past their due day are listed, oldest due first")
    b.add("x2", S([I(1, d0, 30, 20000, [20000])], d0 + 60), lambda o, sc, p: ids(o, []), .25, "x",
          "an invoice paid in full is not overdue")
    if v == 3:
        b.add("x3", S([I(1, d0, 30, 20000, [])], d0 + 33), lambda o, sc, p: ids(o, []), .25, "x",
              "3 days after the due day is still inside the 5 days of grace")
    else:
        b.add("x3", S([I(1, d0, 30, 20000, [])], d0 + 25), lambda o, sc, p: ids(o, []), .25, "x",
              "an invoice still inside its terms is not overdue")
    b.add("partial", S([I(1, d0, 30, 90000, [30000])], d0 + 60), lambda o, sc, p: ids(o, ["inv1"]), 1, "i",
          "an invoice part-paid is still owed in part, so it is still overdue")
    b.add("overpaid", S([I(1, d0, 30, 20000, [25000]), I(2, d0, 30, 8000, [])], d0 + 60),
          lambda o, sc, p: ids(o, ["inv2"]), 1, "i", "an invoice paid more than its amount is not overdue")
    b.add("credit", S([I(1, d0, 30, -5000, []), I(2, d0, 30, 8000, [])], d0 + 60), lambda o, sc, p: ids(o, ["inv2"]), 1, "i",
          "a credit note (negative amount) is not something anyone owes")
    b.add("samecust", S([I(1, d0, 30, 8000, [], "Acme"), I(2, d0 + 5, 30, 8000, [], "Acme")], d0 + 60),
          lambda o, sc, p: ids(o, ["inv1", "inv2"]), 1, "a",
          "two different invoices of one customer are two overdue invoices, both listed")
    if v == 3:
        b.add("graceedge", S([I(1, d0, 30, 20000, [])], d0 + 36), lambda o, sc, p: ids(o, ["inv1"]), 1, "i",
              "6 days after the due day the grace is over")
    g = 5 if v == 3 else 0
    b.add("dueday", S([I(1, d0, 30, 20000, []), I(2, d0 - 1, 30, 20000, [])], d0 + 30 + g),
          lambda o, sc, p: ids(o, ["inv2"]), 1, "i",
          "the stated first overdue day: an invoice is not overdue on the last allowed day, and is the day after")
    pay = ["- paid: the list of payments received so far, in cents ([] if none).",
           "- paid: the total cents received so far (0 if none).",
           "- paid: the list of payments received so far, in cents ([] if none). terms is a label: \"net15\", \"net30\" or "
           "\"net60\" = 15, 30 or 60 days.",
           "- paid: the list of payments received so far, in cents ([] if none)."][v]
    text = (f"Overdue-invoice list for a small business.\n\n    def {fn}(invoices, today) -> list\n\n"
            "- invoices: [{\"id\": str, \"customer\": str, \"issued\": day, \"terms\": days allowed (or a label, see below), "
            "\"amount\": cents, \"paid\": ...}]; days are whole day numbers.\n"
            f"{pay}\n"
            "- An invoice is due `terms` days after it was issued (issued day 100, 30 days: due day 130) and is overdue "
            + ("from the day after its due day" if v != 3 else "once more than 5 days have passed since its due day (due day 130: "
                                                                "overdue from day 136)") + ", unless it has been paid.\n"
            "- Return the ids of the overdue invoices, oldest due date first, ties by id.")
    o = _out(b, text, fn, ["samecust"], ["partial", "overpaid", "credit"] + (["graceedge"] if v == 3 else []), prm)
    o["EXTRA"] = ["dueday"]
    return o


INV_HEAD = '''
PRM = @PARAMS@
_T = {"net15": 15, "net30": 30, "net60": 60}
def _days(t): return _T[t] if isinstance(t, str) else t
_G = 5 if PRM["v"] == 3 else 0
def _paid(i): return i["paid"] if isinstance(i["paid"], (int, float)) else sum(i["paid"])
'''
INV_REF = INV_HEAD + '''
def @FN@(invoices, today):
    seen = {}
    for i in invoices:
        seen[i["id"]] = i
    out = []
    for i in seen.values():
        due = i["issued"] + _days(i["terms"])
        if today > due + _G and i["amount"] > 0 and _paid(i) < i["amount"]:
            out.append((due, i["id"]))
    return [x[1] for x in sorted(out)]
'''
INV_ALT = INV_HEAD + '''
def @FN@(invoices, today):
    rows = {}
    for i in invoices:
        owing = i["amount"] - _paid(i)
        if owing > 0 and i["issued"] + _days(i["terms"]) + _G < today:
            rows[i["id"]] = i["issued"] + _days(i["terms"])
    return sorted(rows, key=lambda k: (rows[k], k))
'''
INV_LIT = INV_HEAD + '''
def @FN@(invoices, today):
    out = []
    for i in invoices:
        unpaid = not i["paid"]
        due = i["issued"] + _days(i["terms"])
        if unpaid and today > due + _G:
            out.append((due, i["id"]))
    return [x[1] for x in sorted(out)]
'''
INV_PAD = INV_HEAD + '''
def @FN@(invoices, today):
    seen = {}
    for i in invoices:
        seen[i["id"]] = i
    out = []; custs = set()
    for i in sorted(seen.values(), key=lambda i: i["issued"] + _days(i["terms"])):
        due = i["issued"] + _days(i["terms"])
        if today > due + _G and i["amount"] > 0 and _paid(i) < i["amount"] and i["customer"] not in custs:
            custs.add(i["customer"]); out.append((due, i["id"]))
    return [x[1] for x in sorted(out)]
'''
INV_NULL = "def @FN@(invoices, today):\n    return []\n"


# ======================================================= SHIPPING COST
def build_shipping(rng, tier):
    fn = rng.choice(["shipping_cents", "parcel_cost", "cart_shipping"])
    v = rng.randrange(4)
    A, B_, C = rng.choice([399, 450]), rng.choice([650, 750]), rng.choice([1200, 1400])
    S_ = rng.choice([150, 200])
    F = rng.choice([3500, 4200])
    prm = {"v": v, "A": A, "B": B_, "C": C, "S": S_, "F": F}
    b = _B()

    def cost(g):
        if g <= 0:
            return 0
        if v == 1:
            return B_ + S_ * -(-g // 500)
        if g <= 500:
            return A
        if g <= 2000:
            return B_
        if g <= 10000:
            return C
        return F if v == 2 else C + S_ * -(-(g - 10000) // 1000)

    def Ln(sku, qty, g, dig=False, price=1000):
        d = {"sku": sku, "qty": qty, "grams": g, "digital": dig}
        if v == 3:
            d["price"] = price
        return d

    def S(cart):
        return [_call(cart)]
    if v == 3:
        b.add("x1", S([Ln("pen", 1, 300, price=6000)]), lambda o, sc, p: _int_is(o, 0), .25, "x",
              "a physical goods total of 5000 cents or more ships free")
    else:
        b.add("x1", S([Ln("pen", 1, 300)]), lambda o, sc, p: _int_is(o, cost(300)), .25, "x", "a small parcel has a small price")
    b.add("x2", S([Ln("pen", 1, 300), Ln("book", 1, 900)]), lambda o, sc, p: _int_is(o, cost(1200)), .25, "x",
          "the lines of the cart are shipped together as one parcel")
    b.add("x3", S([Ln("anvil", 1, 6000), Ln("vase", 1, 7000)]), lambda o, sc, p: _int_is(o, cost(13000)), .25, "x",
          "a very heavy cart takes the heavy-parcel price")
    b.add("alldigital", S([Ln("ebook", 2, 0, True)]), lambda o, sc, p: _int_is(o, 0), 1, "i",
          "a cart of downloads ships nothing, so it costs nothing")
    b.add("digitalmix", S([Ln("pen", 1, 400), Ln("course", 1, 5000, True)]), lambda o, sc, p: _int_is(o, cost(400)), 1, "i",
          "downloads add no weight to the parcel")
    b.add("qtymult", S([Ln("mug", 4, 300)]), lambda o, sc, p: _int_is(o, cost(1200)), 1, "i",
          "four mugs weigh four times one mug")
    b.add("qtyzero", S([Ln("mug", 0, 300)]), lambda o, sc, p: _int_is(o, 0), 1, "i", "nothing in the cart, nothing to ship")
    b.add("empty", S([]), lambda o, sc, p: _int_is(o, 0), 1, "i", "an empty cart costs nothing to ship")
    b.add("dupsku", S([Ln("mug", 1, 300), Ln("mug", 1, 300)]), lambda o, sc, p: _int_is(o, cost(600)), 1, "a",
          "the same item on two cart lines is two items")
    b.add("dupsku2", S([Ln("tin", 1, 800), Ln("tin", 1, 800), Ln("tin", 1, 800)]), lambda o, sc, p: _int_is(o, cost(2400)), 1, "a",
          "three lines of the same tin are three tins")
    if v == 3:
        b.add("digitalfree", S([Ln("pen", 1, 400, price=3000), Ln("course", 1, 5000, True, price=4000)]),
              lambda o, sc, p: _int_is(o, cost(400)), 1, "i", "downloads do not count towards the free-shipping total")
        b.add("freeedge", S([Ln("mug", 5, 300, price=1000)]), lambda o, sc, p: _int_is(o, 0), 1, "i",
              "exactly 5000 cents of goods is enough for free shipping")
    rule = [f"Weight price of one parcel (all physical goods together): up to and including 500 g {A} cents, up to and including "
            f"2000 g {B_} cents, up to and including 10000 g {C} cents; above 10000 g it is {C} cents plus {S_} cents for every "
            "started 1000 g above 10000.",
            f"A parcel costs {B_} cents plus {S_} cents for every started 500 g of its weight (300 g = 1 started 500 g; 500 g = 1; "
            "501 g = 2).",
            f"Weight price of one parcel (all physical goods together): up to and including 500 g {A} cents, up to and including "
            f"2000 g {B_} cents, up to and including 10000 g {C} cents; above 10000 g the order goes as freight for a flat "
            f"{F} cents.",
            f"Weight price of one parcel (all physical goods together): up to and including 500 g {A} cents, up to and including "
            f"2000 g {B_} cents, up to and including 10000 g {C} cents; above 10000 g it is {C} cents plus {S_} cents for every "
            "started 1000 g above 10000. Shipping is free when the physical goods total (qty x price of the lines that are not "
            "downloads) is at least 5000 cents."][v]
    text = (f"Shipping cost for a web shop cart.\n\n    def {fn}(cart) -> int\n\n"
            "- cart: [{\"sku\": str, \"qty\": int, \"grams\": weight of ONE unit, \"digital\": True for a download"
            + (", \"price\": cents per unit" if v == 3 else "") + "}].\n"
            f"- {rule}\n- Return the shipping price in integer cents.")
    return _out(b, text, fn, ["dupsku", "dupsku2"], ["alldigital", "digitalmix", "qtymult", "qtyzero", "empty"]
                + (["digitalfree", "freeedge"] if v == 3 else []), prm)


SH_HEAD = '''
PRM = @PARAMS@
def _cost(g):
    v = PRM["v"]
    if v == 1: return PRM["B"] + PRM["S"] * -(-g // 500)
    if g <= 500: return PRM["A"]
    if g <= 2000: return PRM["B"]
    if g <= 10000: return PRM["C"]
    return PRM["F"] if v == 2 else PRM["C"] + PRM["S"] * -(-(g - 10000) // 1000)
'''
SH_REF = SH_HEAD + '''
def @FN@(cart):
    g = sum(max(0, l["qty"]) * l["grams"] for l in cart if not l.get("digital"))
    if PRM["v"] == 3 and sum(max(0, l["qty"]) * l["price"] for l in cart if not l.get("digital")) >= 5000: return 0
    return _cost(g) if g > 0 else 0
'''
SH_ALT = SH_HEAD + '''
def @FN@(cart):
    total = 0
    for l in cart:
        if l.get("digital") or l["qty"] <= 0:
            continue
        for _ in range(l["qty"]):
            total += l["grams"]
    if PRM["v"] == 3:
        worth = sum(l["price"] * l["qty"] for l in cart if not l.get("digital") and l["qty"] > 0)
        if worth >= 5000: return 0
    return 0 if total == 0 else _cost(total)
'''
SH_LIT = SH_HEAD + '''
def @FN@(cart):
    if PRM["v"] == 3 and sum(l["qty"] * l["price"] for l in cart if not l.get("digital")) >= 5000: return 0
    return _cost(sum(l["qty"] * l["grams"] for l in cart))
'''
SH_PAD = SH_REF.replace('    g = sum(max(0, l["qty"]) * l["grams"] for l in cart if not l.get("digital"))',
                        '    one = {}\n    for l in cart:\n        one.setdefault(l["sku"], l)\n'
                        '    g = sum(max(0, l["qty"]) * l["grams"] for l in one.values() if not l.get("digital"))')
SH_NULL = "def @FN@(cart):\n    return 0\n"


# ======================================================= GRADE AVERAGE
def build_grades(rng, tier):
    fn = rng.choice(["final_grade", "course_grade", "grade_for"])
    v = rng.randrange(3)
    prm = {"v": v}
    b = _B()
    weighted = v != 1

    def It(i, score, mx, w=1):
        d = {"id": f"a{i}", "score": score, "max": mx}
        if weighted:
            d["weight"] = w
        return d

    def final(items):
        seen = {}
        for it in items:
            seen[it["id"]] = it
        ok = [it for it in seen.values() if it["score"] is not None and it["max"] > 0]
        if weighted:
            pct = 100.0 * sum(it["weight"] * it["score"] / it["max"] for it in ok) / sum(it["weight"] for it in ok)
        else:
            pct = 100.0 * sum(it["score"] for it in ok) / sum(it["max"] for it in ok)
        if v == 2:
            return "A" if pct >= 90 else "B" if pct >= 80 else "C" if pct >= 70 else "D" if pct >= 60 else "F"
        return math.floor(pct + 0.5)     # halves up toward +inf (int() truncated negatives; scrub pass-1)

    def S(items):
        return [_call(items)]

    def res(want):
        def j(o, sc, p):
            r = _r0(o)
            if v == 2:
                return isinstance(r, str) and r.strip().upper() == want
            return _isint(r) and int(r) == want
        return j

    def add(i, items, w, kind, why):
        b.add(i, S(items), res(final(items)), w, kind, why)
    if weighted:
        x1 = [It(1, 17, 20, 1), It(2, 70, 100, 3)]
        x2 = [It(1, 44, 50, 2)]
        x3 = [It(1, 16, 20, 1), It(2, 9, 10, 2), It(3, 80, 100, 2)]
        un = [It(1, 17, 20, 1), It(2, None, 100, 3)]
        zm = [It(1, 17, 20, 1), It(2, 0, 0, 2)]
        dp = [It(1, 17, 20, 1), It(1, 17, 20, 1), It(2, 70, 100, 3)]
        tw = [It(1, 8, 10, 2), It(2, 8, 10, 2), It(3, 58, 100, 4)]
        tw2 = [It(1, 9, 10, 1), It(2, 9, 10, 1), It(3, 9, 10, 1), It(4, 44, 100, 3)]
    else:
        x1 = [It(1, 18, 20), It(2, 70, 100)]
        x2 = [It(1, 44, 50)]
        x3 = [It(1, 16, 20), It(2, 9, 10), It(3, 80, 100)]
        un = [It(1, 17, 20), It(2, None, 100)]
        zm = [It(1, 17, 20), It(2, 0, 0)]
        dp = [It(1, 18, 20), It(1, 18, 20), It(2, 70, 100)]
        tw = [It(1, 8, 10), It(2, 8, 10), It(3, 58, 100)]
        tw2 = [It(1, 9, 10), It(2, 9, 10), It(3, 9, 10), It(4, 44, 100)]
    add("x1", x1, .25, "x", "several marks are combined into one grade")
    add("x2", x2, .25, "x", "one mark is its own grade")
    add("x3", x3, .25, "x", "items of different sizes are combined by the stated rule")
    add("ungraded", un, 1, "i", "work that is not marked yet is not a zero")
    add("zeromax", zm, 1, "i", "an item worth no points cannot break the grade")
    add("twin", tw, 1, "a", "two different items with equal marks are two items")
    add("twin2", tw2, 1, "a", "three homeworks with the same mark are three homeworks")
    rule = ["Every item's weight says how much it counts: the grade is the weighted average of score/max over the marked items "
            "(weights of unmarked items do not count), as a whole percent, rounded to nearest, halves up.",
            "The grade is total points earned over total points possible of the marked items, as a whole percent, rounded to "
            "nearest, halves up.",
            "Every item's weight says how much it counts: the grade is the weighted average of score/max over the marked items "
            "(weights of unmarked items do not count). Return a letter from the exact percent: A at 90 or more, B 80, C 70, "
            "D 60, else F."][v]
    fields = ("\"weight\": number" if weighted else "")
    text = (f"Course grade for one student.\n\n    def {fn}(items) -> {'str' if v == 2 else 'int'}\n\n"
            "- items: [{\"id\": str, \"score\": points earned or None if not marked yet, \"max\": points possible"
            + (", " + fields if fields else "") + "}].\n"
            f"- {rule}")
    return _out(b, text, fn, ["twin", "twin2"], ["ungraded", "zeromax"], prm)


GR_HEAD = '''
PRM = @PARAMS@
def _fin(pct):
    if PRM["v"] == 2:
        return "A" if pct >= 90 else "B" if pct >= 80 else "C" if pct >= 70 else "D" if pct >= 60 else "F"
    import math
    return math.floor(pct + 0.5)
def _pct(ok):
    if PRM["v"] == 1:
        return 100.0 * sum(i["score"] for i in ok) / sum(i["max"] for i in ok)
    return 100.0 * sum(i["weight"] * i["score"] / i["max"] for i in ok) / sum(i["weight"] for i in ok)
'''
GR_REF = GR_HEAD + '''
def @FN@(items):
    seen = {}
    for i in items:
        seen[i["id"]] = i
    ok = [i for i in seen.values() if i["score"] is not None and i["max"] > 0]
    return _fin(_pct(ok)) if ok else (0 if PRM["v"] != 2 else "F")
'''
GR_ALT = GR_HEAD + '''
def @FN@(items):
    uniq = {}
    for i in items:
        if i["score"] is not None and i["max"]:
            uniq[i["id"]] = i
    rows = list(uniq.values())
    if not rows:
        return "F" if PRM["v"] == 2 else 0
    return _fin(_pct(rows))
'''
GR_LIT = GR_HEAD + '''
def @FN@(items):
    return _fin(_pct(items))
'''
GR_PAD = GR_REF.replace('seen[i["id"]] = i', 'seen.setdefault((i["score"], i["max"], i.get("weight")), i)')
GR_NULL = "def @FN@(items):\n    return 0\n"

# ======================================================= EVENT RSVP
def build_rsvp(rng, tier):
    fn = rng.choice(["count_rsvps", "rsvp_summary", "headcount"])
    v = rng.randrange(3)
    prm = {"v": v}
    b = _B()
    t0 = rng.randint(100, 900)

    def Rp(i, ans, g, t):
        return {"id": f"p{i}", "answer": ans, "guests": g, "t": t0 + t}

    def final(reps):
        last = {}
        for r in sorted(reps, key=lambda r: r["t"]):
            last[r["id"]] = r
        y = n = m = hc = 0
        for r in last.values():
            g = r["guests"] if isinstance(r["guests"], int) and r["guests"] > 0 else 0
            if v == 1:
                g = min(g, 2)
            if r["answer"] == "yes":
                y += 1
                hc += 1 + g
            elif r["answer"] == "no":
                n += 1
            else:
                m += 1
                if v == 2:
                    hc += 1
        return {"yes": y, "no": n, "maybe": m, "headcount": hc}

    def add(i, reps, w, kind, why):
        want = final(reps)

        def j(o, sc, p, want=want):
            r = _r0(o)
            return isinstance(r, dict) and all(_isint(r.get(k)) and int(r[k]) == want[k] for k in want)
        b.add(i, [_call(reps)], j, w, kind, why)
    add("x1", [Rp(1, "yes", 2, 10), Rp(2, "yes", 0, 11), Rp(3, "no", 0, 12)], .25, "x", "yes, no and guests are counted")
    add("x2", [Rp(1, "maybe", 0, 10), Rp(2, "yes", 1, 11), Rp(3, "maybe", 0, 12), Rp(4, "no", 0, 13)], .25, "x",
        "maybe answers are counted apart")
    if v == 0:
        add("x3", [Rp(1, "yes", 0, 10), Rp(2, "yes", 0, 11), Rp(3, "yes", 0, 12)], .25, "x", "every yes is one attendee")
    elif v == 1:
        add("x3", [Rp(1, "yes", 5, 10), Rp(2, "yes", 1, 11)], .25, "x", "a person's guests stop at the limit")
    else:
        add("x3", [Rp(1, "maybe", 3, 10), Rp(2, "yes", 1, 11)], .25, "x", "a maybe counts as one, without guests")
    add("latest", [Rp(1, "yes", 1, 50), Rp(1, "no", 0, 20), Rp(2, "yes", 0, 30)], 1, "i",
        "the most recent answer of a person is the one that counts, wherever it sits in the list")
    add("changed", [Rp(1, "yes", 3, 10), Rp(1, "no", 0, 20), Rp(2, "yes", 0, 30)], 1, "i",
        "someone who changes yes to no takes their guests with them")
    r0 = Rp(1, "yes", 1, 10)
    add("dupe", [r0, dict(r0), Rp(2, "yes", 0, 20)], 1, "i", "the same form submitted twice is one answer")
    add("badguests", [Rp(1, "yes", None, 10), Rp(2, "yes", -2, 20), Rp(3, "yes", 1, 30)], 1, "i",
        "a missing or negative guest number means no guests")
    add("twins", [Rp(1, "yes", 1, 10), Rp(2, "yes", 1, 10)], 1, "a",
        "two different people who answer alike at the same minute are two people")
    add("twins2", [Rp(1, "yes", 0, 5), Rp(2, "yes", 0, 5), Rp(3, "yes", 0, 5), Rp(4, "maybe", 0, 5)], 1, "a",
        "three friends answering at once are three people")
    add("noguests", [Rp(1, "yes", 1, 10), Rp(2, "no", 2, 11), Rp(3, "maybe", 2, 12)], 1, "i",
        "only the guests of a yes join the headcount, as the brief states")
    rule = ["The headcount is the number of people who said yes plus their guests.",
            "The headcount is the number of people who said yes plus their guests, but each person may bring at most 2 guests "
            "(any more are ignored).",
            "The headcount is the number of people who said yes plus their guests plus one for each person who said maybe "
            "(a maybe's guests do not count)."][v]
    text = (f"RSVP counter for an event.\n\n    def {fn}(replies) -> dict\n\n"
            "- replies: [{\"id\": the person, \"answer\": \"yes\" | \"no\" | \"maybe\", \"guests\": how many extra people they bring, "
            "\"t\": minute the form was sent}], one entry per form sent.\n"
            f"- {rule}\n"
            "- Return {\"yes\": people, \"no\": people, \"maybe\": people, \"headcount\": int}.")
    o = _out(b, text, fn, ["twins", "twins2"], ["latest", "changed", "dupe", "badguests"], prm)
    o["EXTRA"] = ["noguests"]
    return o


RS_HEAD = '''
PRM = @PARAMS@
def _g(r):
    g = r.get("guests")
    g = g if isinstance(g, int) and g > 0 else 0
    return min(g, 2) if PRM["v"] == 1 else g
def _sum(last):
    y = n = m = hc = 0
    for r in last:
        if r["answer"] == "yes": y += 1; hc += 1 + _g(r)
        elif r["answer"] == "no": n += 1
        else:
            m += 1
            if PRM["v"] == 2: hc += 1
    return {"yes": y, "no": n, "maybe": m, "headcount": hc}
'''
RS_REF = RS_HEAD + '''
def @FN@(replies):
    last = {}
    for r in sorted(replies, key=lambda r: r["t"]):
        last[r["id"]] = r
    return _sum(last.values())
'''
RS_ALT = RS_HEAD + '''
def @FN@(replies):
    best = {}
    for r in replies:
        k = r["id"]
        if k not in best or r["t"] >= best[k]["t"]:
            best[k] = r
    return _sum([best[k] for k in sorted(best)])
'''
RS_LIT = RS_HEAD + '''
def @FN@(replies):
    return _sum(replies)
'''
RS_PAD = RS_REF.replace('    return _sum(last.values())', '    return _sum({(r["answer"], r["guests"], r["t"]): r for r in last.values()}.values())')
RS_NULL = "def @FN@(replies):\n    return {}\n"


# ======================================================= LEADERBOARD
def build_board(rng, tier):
    fn = rng.choice(["leaderboard", "top_players", "rank_players"])
    v = rng.randrange(3)
    prm = {"v": v}
    b = _B()
    nm = rng.sample(["Ana", "Ben", "Cy", "Dee", "Eli", "Fay", "Gus", "Hal"], 6)

    def E(i, who, score):
        return {"id": f"e{i}", "player": who, "score": score}

    def table(entries):
        by = {}
        for e in entries:
            if isinstance(e["score"], (int, float)):
                by.setdefault(e["player"], []).append(e["score"])
        sc = {}
        for k, xs in by.items():
            sc[k] = max(xs) if v == 0 else sum(xs) if v == 1 else _half_up_avg(xs)
        return sc

    def ranked(entries, n):
        sc = table(entries)
        order = sorted(sc, key=lambda k: (-sc[k], k))
        out, last, rk = [], None, 0
        for i, k in enumerate(order, 1):
            if sc[k] != last:
                rk, last = i, sc[k]
            if rk <= n:
                out.append((k, rk, sc[k]))
        return out

    def add(i, entries, n, w, kind, why):
        want = ranked(entries, n)

        def j(o, sc, p, want=want):
            r = _r0(o)
            if not isinstance(r, list):
                return False
            got = []
            for x in r:
                # the brief says "score": int: a whole number (26 or 26.0), never an unrounded 25.5
                if not isinstance(x, dict) or not _isint(x.get("rank")) or not _isint(x.get("score")):
                    return False
                got.append((x.get("player"), int(x["rank"]), int(x["score"])))
            return got == want
        b.add(i, [_call(entries, n)], j, w, kind, why)
    a, c, d, e_, f, g = nm
    add("x1", [E(1, a, 50), E(2, c, 80), E(3, d, 65)], 3, .25, "x", "players come back best first with ranks")
    add("x2", [E(1, a, 70), E(2, c, 70), E(3, d, 40), E(4, e_, 55)], 4, .25, "x",
        "equal scores share a rank and the next rank is skipped; ties by name")
    if v == 0:
        add("x3", [E(1, a, 30), E(2, a, 90), E(3, c, 60), E(4, a, 10)], 2, .25, "x", "a player counts with their best entry")
    elif v == 1:
        add("x3", [E(1, a, 30), E(2, a, 20), E(3, c, 60), E(4, d, 45)], 3, .25, "x", "a player counts with the sum of their entries")
    else:
        add("x3", [E(1, a, 30), E(2, a, 21), E(3, c, 22), E(4, d, 45)], 3, .25, "x",
            "a player counts with the average of their entries, to the nearest whole number")
    add("nones", [E(1, a, 40), E(2, a, None), E(3, c, 35), E(4, c, 30), E(5, d, None)], 5, 1, "i",
        "an entry with no score is not a zero: it is left out (and a player with no scored entry is not ranked)")
    add("negative", [E(1, a, -10), E(2, a, -30), E(3, c, -5), E(4, d, -50)], 5, 1, "i",
        "negative scores rank like any other: the least negative is first")
    add("onlynone", [E(1, a, 20), E(2, c, None)], 5, 1, "i", "a player whose entries all lack a score is not on the board")
    add("tieall", [E(1, a, 60), E(2, c, 60), E(3, d, 60), E(4, e_, 40)], 3, 1, "a",
        "all players tied at the top share rank 1; none is dropped")
    add("tieboard", [E(1, a, 90), E(2, c, 70), E(3, d, 70), E(4, e_, 70), E(5, f, 50)], 2, 1, "a",
        "the cut is by rank: every player at rank 2 (here three of them) is returned")
    rule = ["A player's score is their best entry.", "A player's score is the sum of their entries.",
            "A player's score is the average of their entries, rounded to the nearest whole number (halves up)."][v]
    text = (f"Leaderboard for a game.\n\n    def {fn}(entries, n) -> list\n\n"
            "- entries: [{\"id\": str, \"player\": str, \"score\": int or None}], one per game played (None = the game has no score yet).\n"
            f"- {rule} Rank players by score, highest first; players with equal scores share a rank and the next rank is skipped "
            "(1, 2, 2, 4). Order by rank, ties by player name.\n"
            "- Return [{\"player\": str, \"rank\": int, \"score\": int}] for every player whose rank is at most n.")
    return _out(b, text, fn, ["tieall", "tieboard"], ["nones", "negative", "onlynone"], prm)


BD_HEAD = '''
PRM = @PARAMS@
def _score(xs):
    v = PRM["v"]
    if v == 0: return max(xs)
    if v == 1: return sum(xs)
    s, n = sum(xs), len(xs)
    return (2 * s + n) // (2 * n)
'''
BD_REF = BD_HEAD + '''
def @FN@(entries, n):
    by = {}
    for e in entries:
        if isinstance(e.get("score"), (int, float)):
            by.setdefault(e["player"], []).append(e["score"])
    sc = {k: _score(xs) for k, xs in by.items()}
    out, last, rk = [], None, 0
    for i, k in enumerate(sorted(sc, key=lambda k: (-sc[k], k)), 1):
        if sc[k] != last: rk, last = i, sc[k]
        if rk <= n: out.append({"player": k, "rank": rk, "score": sc[k]})
    return out
'''
BD_ALT = BD_HEAD + '''
def @FN@(entries, n):
    groups = {}
    for e in entries:
        s = e.get("score")
        if s is not None:
            groups[e["player"]] = groups.get(e["player"], []) + [s]
    rows = sorted(((-_score(v), p) for p, v in groups.items()))
    res = []
    for pos, (neg, p) in enumerate(rows):
        rank = 1 + sum(1 for r in rows if r[0] < neg)
        if rank <= n: res.append({"player": p, "rank": rank, "score": -neg})
    return res
'''
BD_LIT = BD_HEAD + '''
def @FN@(entries, n):
    by = {}
    for e in entries:
        by.setdefault(e["player"], []).append(e["score"] or 0)
    sc = {k: _score(xs) for k, xs in by.items()}
    out, last, rk = [], None, 0
    for i, k in enumerate(sorted(sc, key=lambda k: (-sc[k], k)), 1):
        if sc[k] != last: rk, last = i, sc[k]
        if rk <= n: out.append({"player": k, "rank": rk, "score": sc[k]})
    return out
'''
BD_PAD = BD_REF.replace('    sc = {k: _score(xs) for k, xs in by.items()}', '    sc = {k: _score(xs) for k, xs in by.items()}\n    _seen = {}\n    for k in sorted(sc): _seen.setdefault(sc[k], k)\n    sc = {k: sc[k] for k in sc if _seen[sc[k]] == k}')
BD_NULL = "def @FN@(entries, n):\n    return []\n"

# ======================================================= SHOP RETURNS
def build_returns(rng, tier):
    fn = rng.choice(["refund_total", "returns_refund", "total_refund"])
    v = rng.randrange(3)
    prm = {"v": v}
    d0 = rng.randint(100, 300)
    b = _B()
    pa, pb = rng.choice([1500, 2000, 2500]), rng.choice([4000, 5000, 6000])

    def O(oid="o1", day=None):
        return {"id": oid, "day": d0 if day is None else day,
                "lines": [{"sku": "mug", "qty": 2, "price": pa}, {"sku": "lamp", "qty": 1, "price": pb},
                                       {"sku": "cup", "qty": 3, "price": pa}]}

    def Sl(i, order, sku, qty, late=0):
        d = {"id": f"s{i}", "order": order, "sku": sku, "qty": qty}
        if v == 2:
            d["day"] = d0 + 10 + late
        return d

    def refund(slips, orders, strict=False):
        left = {(o["id"], l["sku"]): l["qty"] for o in orders for l in o["lines"]}
        price = {(o["id"], l["sku"]): l["price"] for o in orders for l in o["lines"]}
        oday = {o["id"]: o["day"] for o in orders}
        seen, tot = set(), 0
        for sl in slips:
            if sl["id"] in seen:
                continue
            seen.add(sl["id"])
            k = (sl["order"], sl["sku"])
            if k not in left or not isinstance(sl["qty"], int) or sl["qty"] <= 0:
                continue
            if v == 2 and sl["day"] - oday[sl["order"]] > 30:
                continue
            if strict and sl["qty"] > left[k]:
                continue          # the whole slip is refused
            take = min(sl["qty"], left[k])
            left[k] -= take
            amt = price[k] * take
            if v == 1:
                amt -= (amt * 10 + 50) // 100
            tot += amt
        return tot

    def add(i, slips, w, kind, why, orders=None, either=False):
        orders = orders or [O()]
        want = refund(slips, orders)
        alt = refund(slips, orders, True) if either else want
        b.add(i, [_call(orders, slips)], lambda o, sc, p, want=want, alt=alt: _int_is(o, want) or _int_is(o, alt), w, kind, why)
    add("x1", [Sl(1, "o1", "mug", 1)], .25, "x", "a returned item is refunded at the price paid")
    add("x2", [Sl(1, "o1", "mug", 1), Sl(2, "o1", "lamp", 1)], .25, "x", "slips for different items add up")
    if v == 2:
        add("x3", [Sl(1, "o1", "lamp", 1, late=40)], .25, "x", "a slip more than 30 days after the order gets nothing")
    elif v == 1:
        add("x3", [Sl(1, "o1", "lamp", 1)], .25, "x", "10% of each slip's refund is kept as a restocking fee")
    else:
        add("x3", [Sl(1, "o1", "mug", 2)], .25, "x", "a whole line can be returned")
    add("overreturn", [Sl(1, "o1", "mug", 3)], 1, "i",
        "you cannot get back more than you bought (refund what was bought, or refuse the slip)", either=True)
    add("cumulative", [Sl(1, "o1", "mug", 2), Sl(2, "o1", "mug", 1)], 1, "i",
        "the limit is what was bought, over all slips together")
    add("unknown", [Sl(1, "o9", "mug", 1), Sl(2, "o1", "vase", 1), Sl(3, "o1", "mug", 1)], 1, "i",
        "a slip for an order or an item that does not exist is ignored, the rest is refunded")
    add("zeroqty", [Sl(1, "o1", "mug", 0), Sl(2, "o1", "mug", -1), Sl(3, "o1", "mug", 1)], 1, "i",
        "a slip of zero or negative items refunds nothing and never takes money back")
    add("sameid", [Sl(1, "o1", "mug", 1), Sl(1, "o1", "mug", 1)], 1, "i",
        "the same slip number entered twice is one slip")
    add("splitslips", [Sl(1, "o1", "mug", 1), Sl(2, "o1", "mug", 1)], 1, "a",
        "two different slips for one item are two returns, both refunded")
    add("splitslips2", [Sl(1, "o1", "cup", 1), Sl(2, "o1", "cup", 1), Sl(3, "o1", "cup", 1)], 1, "a",
        "three slips of one cup each are three returns")
    rule = ["A refund is the price paid times the number of items returned.",
            "A refund is the price paid times the number of items returned, minus a restocking fee of 10% of that refund "
            "for each slip (rounded half up to the cent).",
            "A refund is the price paid times the number of items returned, but only for slips dated at most 30 days after "
            "the order's day."][v]
    text = (f"Shop returns desk.\n\n    def {fn}(orders, slips) -> int\n\n"
            "- orders: [{\"id\": str, \"day\": whole day number, \"lines\": [{\"sku\": str, \"qty\": bought, \"price\": cents per item}]}].\n"
            "- slips: [{\"id\": the slip number, \"order\": order id, \"sku\": str, \"qty\": items handed back"
            + (", \"day\": day the slip was written" if v == 2 else "") + "}], in the order they were written.\n"
            f"- {rule}\n- Return the total refund for all slips in integer cents.")
    return _out(b, text, fn, ["splitslips", "splitslips2"], ["overreturn", "cumulative", "unknown", "zeroqty", "sameid"], prm)


RT_HEAD = '''
PRM = @PARAMS@
def _amt(price, take):
    a = price * take
    if PRM["v"] == 1: a -= (a * 10 + 50) // 100
    return a
'''
RT_REF = RT_HEAD + '''
def @FN@(orders, slips):
    left = {(o["id"], l["sku"]): l["qty"] for o in orders for l in o["lines"]}
    price = {(o["id"], l["sku"]): l["price"] for o in orders for l in o["lines"]}
    oday = {o["id"]: o["day"] for o in orders}
    seen = set(); tot = 0
    for sl in slips:
        if sl["id"] in seen: continue
        seen.add(sl["id"])
        k = (sl["order"], sl["sku"])
        if k not in left or sl["qty"] <= 0: continue
        if PRM["v"] == 2 and sl["day"] - oday[sl["order"]] > 30: continue
        take = min(sl["qty"], left[k]); left[k] -= take
        tot += _amt(price[k], take)
    return tot
'''
RT_ALT = RT_HEAD + '''
def @FN@(orders, slips):
    cat = {}
    for o in orders:
        for l in o["lines"]:
            cat[(o["id"], l["sku"])] = [l["qty"], l["price"], o["day"]]
    done = {}
    for sl in slips:
        done.setdefault(sl["id"], sl)
    total = 0
    for sid in done:
        sl = done[sid]
        row = cat.get((sl["order"], sl["sku"]))
        if row is None or sl["qty"] < 1: continue
        if PRM["v"] == 2 and sl["day"] > row[2] + 30: continue
        n = min(row[0], sl["qty"]); row[0] -= n
        total += _amt(row[1], n)
    return total
'''
RT_LIT = RT_HEAD + '''
def @FN@(orders, slips):
    tot = 0
    for sl in slips:
        for o in orders:
            if o["id"] != sl["order"]: continue
            for l in o["lines"]:
                if l["sku"] == sl["sku"]:
                    if PRM["v"] == 2 and sl["day"] - o["day"] > 30: continue
                    tot += _amt(l["price"], sl["qty"])
    return tot
'''
RT_PAD = RT_REF.replace('seen.add(sl["id"])', 'seen.add(sl["id"]); _k = (sl["order"], sl["sku"], sl["qty"])\n        if _k in _d: continue\n        _d.add(_k)').replace("seen = set(); tot = 0", "seen = set(); tot = 0; _d = set()")
RT_NULL = "def @FN@(orders, slips):\n    return 0\n"


# ======================================================= LOYALTY POINTS LEDGER
def _cancelled(ev):
    """ids cancelled by a reverse that is itself standing: a reverse of a reverse undoes the first reverse ("as if that
    event never happened"), so its target stands again (scrub pass-1: a plain set of every `of` cancelled it anyway)"""
    by = {e["id"]: e for e in ev}
    memo = {}

    def stands(i, depth=0):
        if i in memo:
            return memo[i]
        memo[i] = True                                      # a cycle of reverses: treat as standing (never in a scenario)
        r = not any(e["kind"] == "reverse" and e.get("of") == i and stands(e["id"], depth + 1) for e in ev) if depth < 50 else True
        memo[i] = r
        return r
    return {i for i in by if not stands(i)}


def build_ledger(rng, tier):
    fn = rng.choice(["points_balance", "loyalty_balance", "balance_on"])
    v = rng.randrange(3)
    prm = {"v": v}
    D = rng.randint(1000, 3000)
    b = _B()

    def Ev(i, day, kind, pts=None, of=None):
        e = {"id": f"e{i}", "day": D + day, "kind": kind}
        if pts is not None:
            e["points"] = pts
        if of is not None:
            e["of"] = f"e{of}"
        return e

    def balance(events, today):
        seen, ev = set(), []
        for e in events:
            if e["id"] not in seen:
                seen.add(e["id"]); ev.append(e)
        gone = _cancelled(ev)
        live = [e for e in ev if e["kind"] != "reverse" and e["id"] not in gone]
        live = [e for _, e in sorted(enumerate(live), key=lambda t: (t[1]["day"], t[0]))]
        lots = []
        for e in live:
            if e["kind"] == "earn":
                pts = e["points"] + (e["points"] // 10 if v == 2 and e["points"] >= 1000 else 0)
                lots.append([e["day"], pts])
            else:
                if v == 1 and e["points"] < 100:
                    continue
                need = e["points"]
                for lot in lots:
                    if need <= 0:
                        break
                    if e["day"] - lot[0] >= 365 or lot[1] <= 0:
                        continue
                    t = min(need, lot[1]); lot[1] -= t; need -= t
        return sum(l[1] for l in lots if today - l[0] < 365)

    def add(i, events, today, w, kind, why):
        want = balance(events, D + today)
        b.add(i, [_call(events, D + today)], lambda o, sc, p, want=want: _int_is(o, want), w, kind, why)
    add("x1", [Ev(1, 0, "earn", 500)], 10, .25, "x", "earned points are in the balance")
    add("x2", [Ev(1, 0, "earn", 500), Ev(2, 5, "redeem", 200)], 10, .25, "x", "redeemed points leave the balance")
    if v == 0:
        add("x3", [Ev(1, 0, "earn", 300)], 400, .25, "x", "points expire 365 days after the day they were earned")
    elif v == 1:
        add("x3", [Ev(1, 0, "earn", 500), Ev(2, 3, "redeem", 50)], 10, .25, "x", "a redemption under 100 points is refused")
    else:
        add("x3", [Ev(1, 0, "earn", 1000)], 10, .25, "x", "an earn of 1000 or more gets 10% extra, rounded down")
    add("oldfirst", [Ev(1, 0, "earn", 400), Ev(2, 100, "earn", 400), Ev(3, 200, "redeem", 300)], 380, 1, "i",
        "redemptions use the oldest points first, so the older lot's rest expires and the newer lot is untouched")
    add("reverse", [Ev(1, 0, "earn", 500), Ev(2, 1, "earn", 300), Ev(3, 2, "reverse", of=1)], 10, 1, "i",
        "a reversal cancels the earn it names")
    add("reverseredeem", [Ev(1, 0, "earn", 500), Ev(2, 1, "redeem", 200), Ev(3, 2, "reverse", of=2)], 10, 1, "i",
        "reversing a redemption gives the points back")
    add("unsorted", [Ev(2, 5, "redeem", 200), Ev(1, 0, "earn", 500)], 10, 1, "i",
        "events are in day order whatever order they were listed in")
    add("sameid", [Ev(1, 0, "earn", 500), Ev(1, 0, "earn", 500)], 10, 1, "i", "the same event id twice is one event")
    add("twoearns", [Ev(1, 0, "earn", 500), Ev(2, 0, "earn", 500)], 10, 1, "a",
        "two different earns of the same size on one day are two earns")
    add("twoearns2", [Ev(1, 3, "earn", 200), Ev(2, 3, "earn", 200), Ev(3, 3, "earn", 200), Ev(4, 9, "redeem", 100)], 12, 1, "a",
        "three equal earns on one day, then a redemption, leave 500")
    add("rerev", [Ev(1, 0, "earn", 500), Ev(2, 1, "reverse", of=1), Ev(3, 2, "reverse", of=2)], 10, 1, "i",
        "a reverse that is itself reversed never happened, so the earn it named stands")
    rule = ["Points expire 365 days after the day they were earned (earned on day 100: usable through day 464, gone from day 465).",
            "Points expire 365 days after the day they were earned (earned on day 100: usable through day 464, gone from day 465). "
            "A redemption of fewer than 100 points is refused and changes nothing.",
            "Points expire 365 days after the day they were earned (earned on day 100: usable through day 464, gone from day 465). "
            "An earn event of 1000 or more points also gets 10% extra points, rounded down, expiring with it."][v]
    text = (f"Loyalty points balance.\n\n    def {fn}(events, today) -> int\n\n"
            "- events: [{\"id\": str, \"day\": whole day number, \"kind\": \"earn\" | \"redeem\" | \"reverse\", \"points\": int "
            "(earn, redeem), \"of\": id of the event it cancels (reverse)}], in no particular order.\n"
            f"- {rule} A redemption spends the oldest points that have not expired first. A reverse event cancels the earlier "
            "event it names as if that event never happened.\n"
            "- Return the points the customer holds on day `today` (every event is on or before it).")
    o = _out(b, text, fn, ["twoearns", "twoearns2"], ["oldfirst", "reverse", "reverseredeem", "unsorted", "sameid"], prm)
    o["EXTRA"] = ["oldfirst", "rerev"]
    return o


LG_HEAD = '''
PRM = @PARAMS@
def _run(events, today):
    seen = set(); ev = []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"]); ev.append(e)
    def stands(i, seen=()):
        return i in seen or not any(r["kind"] == "reverse" and r.get("of") == i and stands(r["id"], seen + (i,)) for r in ev)
    gone = {e["id"] for e in ev if not stands(e["id"])}
    live = [e for e in ev if e["kind"] != "reverse" and e["id"] not in gone]
    live = [e for _, e in sorted(enumerate(live), key=lambda t: (t[1]["day"], t[0]))]
    lots = []
    for e in live:
        if e["kind"] == "earn":
            p = e["points"]
            if PRM["v"] == 2 and p >= 1000: p += p // 10
            lots.append([e["day"], p])
        else:
            if PRM["v"] == 1 and e["points"] < 100: continue
            need = e["points"]
            for lot in lots:
                if need <= 0: break
                if e["day"] - lot[0] >= 365 or lot[1] <= 0: continue
                t = min(need, lot[1]); lot[1] -= t; need -= t
    return sum(l[1] for l in lots if today - l[0] < 365)
'''
LG_REF = LG_HEAD + '''
def @FN@(events, today):
    return _run(events, today)
'''
LG_ALT = LG_HEAD.split("def _run")[0] + '''
def @FN@(events, today):
    byid = {}
    for e in events: byid.setdefault(e["id"], e)
    def standing(i, path=()):
        return i in path or not any(r["kind"] == "reverse" and r.get("of") == i and standing(r["id"], path + (i,))
                                    for r in byid.values())
    cancelled = {i for i in byid if not standing(i)}
    seq = sorted((e for e in byid.values() if e["kind"] != "reverse" and e["id"] not in cancelled), key=lambda e: e["day"])
    lots = []
    for e in seq:
        if e["kind"] == "earn":
            bonus = e["points"] // 10 if PRM["v"] == 2 and e["points"] >= 1000 else 0
            lots.append({"day": e["day"], "left": e["points"] + bonus})
        elif not (PRM["v"] == 1 and e["points"] < 100):
            todo = e["points"]
            for lot in lots:
                if todo and e["day"] < lot["day"] + 365 and lot["left"]:
                    take = min(todo, lot["left"]); lot["left"] -= take; todo -= take
    return sum(l["left"] for l in lots if today < l["day"] + 365)
'''
LG_LIT = LG_HEAD.split("def _run")[0] + '''
def @FN@(events, today):
    tot = 0
    for e in events:
        if e["kind"] == "earn" and today - e["day"] < 365:
            p = e["points"]
            if PRM["v"] == 2 and p >= 1000: p += p // 10
            tot += p
        elif e["kind"] == "redeem" and not (PRM["v"] == 1 and e["points"] < 100):
            tot -= e["points"]
    return tot
'''
LG_PAD = LG_REF.replace('if e["id"] not in seen:\n            seen.add(e["id"]); ev.append(e)', 'k = (e["day"], e["kind"], e.get("points"), e.get("of"))\n        if k not in seen:\n            seen.add(k); ev.append(e)')
LG_NULL = "def @FN@(events, today):\n    return 0\n"


# ======================================================= EXPENSE REIMBURSEMENT
def build_expenses(rng, tier):
    fn = rng.choice(["reimbursement", "pay_out_now", "expense_payout"])
    v = rng.randrange(3)
    prm = {"v": v}
    b = _B()

    def Ln(i, amount, approved, cat="travel", rec=True):
        d = {"id": f"x{i}", "category": cat, "amount": amount, "approved": approved}
        if v == 2:
            d["has_receipt"] = rec
        return d

    def payout(lines):
        seen, tot, meals = set(), 0, 0
        for l in lines:
            if l["id"] in seen:
                continue
            seen.add(l["id"])
            if l["approved"] is None:
                continue
            a = min(l["approved"], l["amount"]) if l["amount"] >= 0 else min(max(l["approved"], l["amount"]), 0)
            if v == 2 and l["amount"] > 2500 and not l["has_receipt"]:
                a = min(a, 2500)
            if v == 1 and l["category"] == "meals":
                meals += a
                continue
            tot += a
        if v == 1:
            tot += min(meals, 8000)
        return tot

    def add(i, lines, w, kind, why):
        want = payout(lines)
        b.add(i, [_call(lines)], lambda o, sc, p, want=want: _int_is(o, want), w, kind, why)
    add("x1", [Ln(1, 4000, 4000), Ln(2, 1500, 1500)], .25, "x", "approved lines are paid")
    add("x2", [Ln(1, 4000, 2500), Ln(2, 1500, 1500)], .25, "x", "a line approved for less than claimed is paid the approved amount")
    if v == 1:
        add("x3", [Ln(1, 5000, 5000, "meals"), Ln(2, 7000, 7000, "meals"), Ln(3, 1000, 1000)], .25, "x",
            "meals together are paid at most 8000 cents")
    elif v == 2:
        add("x3", [Ln(1, 4000, 4000, rec=False), Ln(2, 1800, 1800, rec=False)], .25, "x",
            "a line over 2500 with no receipt is paid at most 2500; a small one is paid in full")
    else:
        add("x3", [Ln(1, 1200, 0), Ln(2, 800, 800)], .25, "x", "a line approved for nothing is paid nothing")
    add("overapproved", [Ln(1, 2000, 3500), Ln(2, 1000, 1000)], 1, "i", "nobody is paid more than they claimed")
    add("pending", [Ln(1, 2000, None), Ln(2, 1000, 1000)], 1, "i",
        "a line nobody has looked at yet is not paid now, and it is not a zero that breaks the rest")
    add("credit", [Ln(1, 3000, 3000), Ln(2, -1000, -1000)], 1, "i", "a vendor credit (negative line) reduces what is paid out")
    add("sameid", [Ln(1, 2000, 2000), Ln(1, 2000, 2000)], 1, "i", "the same line id entered twice is one line")
    add("twin", [Ln(1, 2000, 2000), Ln(2, 2000, 2000)], 1, "a", "two different lines for the same amount are two expenses")
    add("twin2", [Ln(1, 900, 900, "other"), Ln(2, 900, 900, "other"), Ln(3, 900, 900, "other")], 1, "a",
        "three taxi rides of the same price are three rides")
    rule = ["Each line is paid the approved amount, but never more than the amount claimed. A line with approved = None has not "
            "been reviewed yet and is not paid now.",
            "Each line is paid the approved amount, but never more than the amount claimed. A line with approved = None has not "
            "been reviewed yet and is not paid now. All \"meals\" lines together are paid at most 8000 cents.",
            "Each line is paid the approved amount, but never more than the amount claimed. A line with approved = None has not "
            "been reviewed yet and is not paid now. A line over 2500 cents without a receipt is paid at most 2500."][v]
    text = (f"Expense report payout.\n\n    def {fn}(lines) -> int\n\n"
            "- lines: [{\"id\": str, \"category\": str, \"amount\": cents claimed (negative for a vendor credit), "
            "\"approved\": cents approved or None" + (", \"has_receipt\": bool" if v == 2 else "") + "}].\n"
            f"- {rule}\n- Return the total to pay now, in integer cents.")
    o = _out(b, text, fn, ["twin", "twin2"], ["overapproved", "pending", "credit", "sameid"], prm)
    o["EXTRA"] = ["pending"]
    return o


EX_HEAD = '''
PRM = @PARAMS@
'''
EX_REF = EX_HEAD + '''
def @FN@(lines):
    seen = set(); tot = 0; meals = 0
    for l in lines:
        if l["id"] in seen: continue
        seen.add(l["id"])
        if l["approved"] is None: continue
        a = min(l["approved"], l["amount"]) if l["amount"] >= 0 else min(max(l["approved"], l["amount"]), 0)
        if PRM["v"] == 2 and l["amount"] > 2500 and not l["has_receipt"]: a = min(a, 2500)
        if PRM["v"] == 1 and l["category"] == "meals":
            meals += a; continue
        tot += a
    if PRM["v"] == 1: tot += min(meals, 8000)
    return tot
'''
EX_ALT = EX_HEAD + '''
def @FN@(lines):
    first = {}
    for l in lines: first.setdefault(l["id"], l)
    paid = []; meals = 0
    for l in first.values():
        a = l["approved"]
        if a is None: continue
        a = (a if a <= l["amount"] else l["amount"]) if l["amount"] >= 0 else min(max(a, l["amount"]), 0)
        if PRM["v"] == 2 and l["amount"] > 2500 and not l["has_receipt"] and a > 2500: a = 2500
        if PRM["v"] == 1 and l["category"] == "meals": meals += a
        else: paid.append(a)
    return sum(paid) + (min(meals, 8000) if PRM["v"] == 1 else 0)
'''
EX_LIT = EX_HEAD + '''
def @FN@(lines):
    tot = 0; meals = 0
    for l in lines:
        a = l["approved"] or 0
        a = min(a, l["amount"]) if a >= 0 else a
        if PRM["v"] == 2 and l["amount"] > 2500 and not l["has_receipt"]: a = min(a, 2500)
        if PRM["v"] == 1 and l["category"] == "meals": meals += a
        else: tot += a
    return tot + (min(meals, 8000) if PRM["v"] == 1 else 0)
'''
EX_PAD = EX_REF.replace('if l["id"] in seen: continue\n        seen.add(l["id"])', 'k = (l["category"], l["amount"], l["approved"])\n        if k in seen: continue\n        seen.add(k)')
EX_NULL = "def @FN@(lines):\n    return 0\n"


BUILD = {"expenses": build_expenses, "returns": build_returns, "ledger": build_ledger, "board": build_board, "mileage": build_mileage, "invoices": build_invoices,
         "shipping": build_shipping, "grades": build_grades, "rsvp": build_rsvp}
# (ref, lit, pad, null, alt)
SRC = {"expenses": (EX_REF, EX_LIT, EX_PAD, EX_NULL, EX_ALT),
       "returns": (RT_REF, RT_LIT, RT_PAD, RT_NULL, RT_ALT),
       "ledger": (LG_REF, LG_LIT, LG_PAD, LG_NULL, LG_ALT),
       "board": (BD_REF, BD_LIT, BD_PAD, BD_NULL, BD_ALT),
       "mileage": (MILE_REF, MILE_LIT, MILE_PAD, MILE_NULL, MILE_ALT),
       "invoices": (INV_REF, INV_LIT, INV_PAD, INV_NULL, INV_ALT),
       "shipping": (SH_REF, SH_LIT, SH_PAD, SH_NULL, SH_ALT),
       "grades": (GR_REF, GR_LIT, GR_PAD, GR_NULL, GR_ALT),
       "rsvp": (RS_REF, RS_LIT, RS_PAD, RS_NULL, RS_ALT)}
