import pytest
from datetime import date
from datetime import date, timedelta
from _pk import o_shares, rng
from potkeeper.household import Household, Member
from potkeeper.ledger import Ledger
from potkeeper.reports import year_end


JSON = {"members": [{"name": "Ana", "weight": 2}, {"name": "Ben", "weight": 1}, {"name": "Mika", "weight": 0},
                    {"name": "Cleo", "weight": 1}],
        "changes": [{"from": "2026-09-01", "weights": {"Mika": 1}},
                    {"from": "2026-07-01", "weights": {"Ben": 2, "Cleo": 0}},
                    {"from": "2026-09-01", "weights": {"Mika": 2, "Ana": 1}}]}


def test_weights_on():
    hh = Household.from_json(JSON)
    assert hh.weights_on(date(2026, 1, 1)) == [2, 1, 0, 1]
    assert hh.weights_on(date(2026, 6, 30)) == [2, 1, 0, 1]
    assert hh.weights_on(date(2026, 7, 1)) == [2, 2, 0, 0]
    assert hh.weights_on(date(2026, 8, 31)) == [2, 2, 0, 0]
    assert hh.weights_on(date(2026, 9, 1)) == [1, 2, 2, 0]
    assert hh.weights_on(date(2027, 1, 1)) == [1, 2, 2, 0]
    assert hh.weights == [2, 1, 0, 1]


def test_validation():
    with pytest.raises(ValueError):
        Household.from_json({"members": [{"name": "A"}], "changes": [{"from": "2026-01-01", "weights": {"Z": 1}}]})
    with pytest.raises(ValueError):
        Household.from_json({"members": [{"name": "A"}, {"name": "B", "weight": 0}],
                             "changes": [{"from": "2026-03-01", "weights": {"A": 0}}]})
    with pytest.raises(ValueError):
        Household.from_json({"members": [{"name": "A"}], "changes": [{"from": "2026-03-01", "weights": {"A": -1}}]})
    with pytest.raises(ValueError):
        Household([Member("A", -1), Member("B", 2)])
    ok = Household.from_json({"members": [{"name": "A"}, {"name": "B", "weight": 0}],
                              "changes": [{"from": "2026-03-01", "weights": {"A": 0, "B": 1}}]})
    assert ok.weights_on(date(2026, 3, 1)) == [0, 1]


def test_rounded_once_per_year():
    hh = Household([Member("Ana", 1), Member("Ben", 1), Member("Cleo", 1)])
    led = Ledger()
    for m in range(1, 13):
        led.add(date(2026, m, 5), -1000, "Internet", shared=True, paid_by="Ana")
    led.add(date(2026, 6, 1), -4000, "Plants", category="garden")      # not shared
    r = year_end(led, hh, 2026)
    assert r["members"] == [{"name": "Ana", "paid": 12000, "share": 4000, "balance": 8000},
                            {"name": "Ben", "paid": 0, "share": 4000, "balance": -4000},
                            {"name": "Cleo", "paid": 0, "share": 4000, "balance": -4000}]


def test_weight_change_mid_year():
    hh = Household.from_json({"members": [{"name": "Ana", "weight": 2}, {"name": "Ben", "weight": 1},
                                           {"name": "Mika", "weight": 0}],
                              "changes": [{"from": "2026-07-01", "weights": {"Ben": 2, "Mika": 1}}]})
    led = Ledger()
    led.add(date(2026, 6, 30), -90001, "Rent", shared=True, paid_by="Ana")
    led.add(date(2026, 7, 1), -90001, "Rent", shared=True, paid_by="Ana")
    led.add(date(2026, 7, 9), 1001, "Refund", shared=True, paid_by="Ben")
    r = year_end(led, hh, 2026)
    # Ana 60000.67 + 36000.4 - 400.4 ; Ben 30000.33 + 36000.4 - 400.4 ; Mika 18000.2 - 200.2
    assert [m["share"] for m in r["members"]] == [95601, 65600, 17800]
    assert [m["paid"] for m in r["members"]] == [180002, -1001, 0]
    assert sum(m["balance"] for m in r["members"]) == 0


def test_random_against_readme():
    r = rng(41)
    for _ in range(150):
        n = r.randint(2, 5)
        names = ["M%d" % i for i in range(n)]
        base = [r.choice([0, 1, 1, 2, 3]) for _ in range(n)]
        base[r.randrange(n)] = max(1, base[0])
        changes = []
        for _ in range(r.randint(0, 3)):
            w = {names[i]: r.choice([0, 1, 2, 3]) for i in r.sample(range(n), r.randint(1, n))}
            changes.append((date(2026, r.randint(1, 12), r.randint(1, 28)), w))
        try:
            hh = Household.from_json({"members": [{"name": nm, "weight": w} for nm, w in zip(names, base)],
                                      "changes": [{"from": d.isoformat(), "weights": w} for d, w in changes]})
        except ValueError:
            continue
        led = Ledger()
        shared = []
        for _ in range(r.randint(1, 25)):
            d = date(2026, 1, 1) + timedelta(days=r.randint(0, 364))
            c = r.choice([-1, -1, -1, 1]) * r.randint(1, 200000)
            by = r.choice(names)
            led.add(d, c, "x", shared=True, paid_by=by)
            shared.append((d, c, by))
        led.add(date(2025, 12, 31), -777, "old", shared=True, paid_by=names[0])
        got = year_end(led, hh, 2026)["members"]
        paid, share = o_shares(names, base, changes, shared)
        assert [m["paid"] for m in got] == paid
        assert [m["share"] for m in got] == share, (base, changes, shared)


# ---- settle
from _pk import o_settle, rand_balances, rng
from potkeeper.reports import settle


def _apply(names, bal, transfers):
    bal = dict(zip(names, bal))
    for frm, to, c in transfers:
        assert c > 0 and bal[frm] < 0 and bal[to] > 0
        bal[frm] += c
        bal[to] -= c
    return bal


def test_fewest_transfers_known():
    names = list("ABCDEF")
    for bal, n in (([-5, 5, -3, 3, 0, 0], 2), ([10, -4, -6, 4, 6, -10], 3), ([1, 2, 3, -6, 0, 0], 3),
                   ([-7, 3, 4, -2, 1, 1], 4)):
        got = settle(names, bal)
        assert all(v == 0 for v in _apply(names, bal, got).values())
        assert len(got) == n, (bal, got)


def test_fewest_transfers_random():
    r = rng(51)
    for _ in range(300):
        n = r.randint(2, 10)
        names = ["P%d" % i for i in range(n)]
        bal = rand_balances(r, n)
        got = settle(names, bal)
        assert all(v == 0 for v in _apply(names, bal, got).values()), bal
        assert len(got) == len(o_settle(names, bal)), bal


def test_canonical_known():
    n = list("ABCDEF")
    assert settle(n, [10, -4, -6, 4, 6, -10]) == [("F", "A", 10), ("B", "D", 4), ("C", "E", 6)]
    assert settle(n, [-5, 5, -5, 5, 0, 0]) == [("A", "B", 5), ("C", "D", 5)]
    assert settle(n, [3, -3, 3, -3, 2, -2]) == [("B", "A", 3), ("D", "C", 3), ("F", "E", 2)]
    assert settle(n, [6, -2, -4, 2, 4, -6]) == [("F", "A", 6), ("B", "D", 2), ("C", "E", 4)]


def test_canonical_random():
    r = rng(61)
    for _ in range(400):
        k = r.randint(2, 10)
        names = ["P%d" % i for i in range(k)]
        bal = rand_balances(r, k)
        assert settle(names, bal) == o_settle(names, bal), bal


# ---- end to end
from datetime import date

from potkeeper.household import Household, Member
from potkeeper.importer import import_csv
from potkeeper.ledger import Ledger
from potkeeper.recurring import Recurring, materialise
from potkeeper.reports import settle, year_end
from potkeeper.rules import Rules
from potkeeper.split import split_cents
from _pk import o_split, rng

HH_FILE = {"members": [{"name": "Ana", "weight": 2}, {"name": "Ben", "weight": 1},
                       {"name": "Mika", "weight": 0, "adult": False}, {"name": "Cleo", "weight": 1}],
           "changes": [{"from": "2026-09-01", "weights": {"Mika": 1}}]}
HH = Household.from_json(HH_FILE)

DEC = """\ufeffdate,amount,payee,memo
03.12.2026,-4.20,Kaffeeklatsch,
03.12.2026,-4.20,Kaffeeklatsch,
10.12.2026,"(1,250.01)",Moebel  Haus,sofa
11.12.2026,0.29,Stadtwerke,bonus
31.12.2026,-100.03,REWE To Go,
01.01.2027,-77.00,Rewe,new year
"""

MEMBERS = [{"name": "Ana", "paid": 1140012, "share": 570995, "balance": 569017},
           {"name": "Ben", "paid": 105003, "share": 285498, "balance": -180495},
           {"name": "Mika", "paid": 0, "share": 102996, "balance": -102996},
           {"name": "Cleo", "paid": -29, "share": 285497, "balance": -285526}]
SETTLE = [("Cleo", "Ana", 285526), ("Ben", "Ana", 180495), ("Mika", "Ana", 102996)]

RULES = "rewe => groceries\nrewe to go => takeaway\nmoebel => home\nkaffee => cafe\nstadtwerke => utilities\n"


def build():
    led = Ledger()
    import_csv(led, DEC)
    for t in led:
        if t.payee.startswith(("Moebel", "REWE")):
            t.shared, t.paid_by = True, "Ben"
        if t.payee == "Stadtwerke":
            t.shared, t.paid_by = True, "Cleo"
    rent = Recurring(date(2026, 1, 31), "monthly", -95001, "Rent", category="rent", shared=True, paid_by="Ana")
    materialise(led, rent, date(2026, 1, 1), date(2026, 12, 31))
    led.add(date(2026, 6, 30), 30001, "Moebel Haus", category="home", shared=True, paid_by="Ben", memo="refund")
    Rules.parse(RULES).categorise(led)
    return led


def test_year_end_2026():
    r = year_end(build(), HH, 2026)
    assert r["members"] == MEMBERS
    assert r["settle"] == SETTLE
    assert r["by_category"] == [("rent", 1140012), ("home", 95000), ("takeaway", 10003), ("cafe", 840),
                                ("utilities", -29)]


def test_import_twice_same_year_end():
    led = build()
    before = year_end(led, HH, 2026)
    import_csv(led, DEC)
    assert year_end(led, HH, 2026) == before


def test_settle_ties_in_household_order():
    assert settle(["A", "B", "C", "D"], [-5, 5, -5, 5]) == [("A", "B", 5), ("C", "D", 5)]
    assert settle(["A", "B", "C"], [4, -2, -2]) == [("B", "A", 2), ("C", "A", 2)]
    assert settle(["A", "B", "C", "D"], [3, 3, -3, -3]) == [("C", "A", 3), ("D", "B", 3)]


def test_cli_year_end_json(tmp_path):
    from potkeeper import cli, store
    import io, json
    store.save_ledger(build(), str(tmp_path / "ledger.json"))
    (tmp_path / "household.json").write_text(json.dumps(HH_FILE), encoding="utf-8")
    out = io.StringIO()
    cli.main(["--ledger", str(tmp_path / "ledger.json"), "--household", str(tmp_path / "household.json"),
              "year-end", "2026", "--json"], out=out)
    r = json.loads(out.getvalue())
    assert r["members"] == MEMBERS
    assert r["settle"] == [list(t) for t in SETTLE]
