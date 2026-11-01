import pytest
from datetime import date
from _pk import fmt_commas, fmt_plain, rng
from potkeeper.importer import import_csv
from potkeeper.ledger import Ledger
from potkeeper.money import parse_amount
from potkeeper.rules import Rules, normalise_payee


STATEMENT = """Date,Payee,Amount,Memo
03.12.2026,Kaffeeklatsch,-4.20,card 1
03.12.2026,Kaffeeklatsch,-4.20,card 2
03.12.2026,Bakery Holm,-6.35,
04.12.2026,KAFFEEKLATSCH ,-4.20,card 3
"""


def test_identical_purchases_kept():
    led = Ledger()
    r = import_csv(led, STATEMENT)
    assert (r.added, r.skipped) == (4, 0)
    assert [(t.id, t.date, t.cents, t.memo) for t in led] == [
        (1, date(2026, 12, 3), -420, "card 1"), (2, date(2026, 12, 3), -420, "card 2"),
        (3, date(2026, 12, 3), -635, ""), (4, date(2026, 12, 4), -420, "card 3")]


def test_reimport_adds_nothing():
    led = Ledger()
    import_csv(led, STATEMENT)
    r = import_csv(led, STATEMENT)
    assert (r.added, r.skipped) == (0, 4)
    assert len(led) == 4


def test_partial_overlap_skips_first_rows():
    led = Ledger()
    led.add(date(2026, 12, 3), -420, "kaffeeklatsch", memo="typed by hand")
    text = ("date,amount,payee,memo\n2026-12-03,-4.20,Kaffee  Klatsch,a\n"
            "2026-12-03,-4.20,KaffeeKlatsch,b\n2026-12-03,-4.20,kaffeeklatsch,c\n2026-12-03,-4.20,Kaffeeklatsch,d\n")
    r = import_csv(led, text)
    # "Kaffee  Klatsch" normalises to "kaffee klatsch": a different payee
    assert (r.added, r.skipped) == (3, 1)
    assert [(t.id, t.memo) for t in led] == [(1, "typed by hand"), (2, "a"), (3, "c"), (4, "d")]


def test_counts_per_key():
    led = Ledger()
    for _ in range(2):
        led.add(date(2026, 12, 5), -100, "Kiosk")
    rows = "".join("2026-12-05,-1.00,Kiosk,m%d\n" % i for i in range(5))
    r = import_csv(led, "date,amount,payee,memo\n" + rows)
    assert (r.added, r.skipped) == (3, 2)
    assert [t.memo for t in led][2:] == ["m2", "m3", "m4"]


def test_byte_order_mark_ignored():
    led = Ledger()
    r = import_csv(led, "﻿Date,Amount,Payee\n2026-12-07,-12.00,Kino\n")
    assert (r.added, r.skipped) == (1, 0)
    assert led.get(1).cents == -1200


def test_amounts_exact_and_ascii_only():
    r = rng(11)
    for _ in range(3000):
        c = r.choice([r.randint(-99999, 99999), r.randint(-10**9, 10**9)])
        assert parse_amount(fmt_plain(c)) == c and parse_amount(fmt_commas(c)) == c
    assert parse_amount("0.29") == 29 and parse_amount("(1,000.07)") == -100007
    for bad in ["１２.50", "٣.00", "12.٥٠", "1,२३४.00", "", "12.", "1.234", "(-5)"]:
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_payees_casefold():
    assert normalise_payee("  Straßenbahn   Ticket ") == "strassenbahn ticket"
    assert Rules.parse("strasse => transport\n").match("STRASSENBAHN") == "transport"
    assert Rules.parse("STRASSE => transport\n").match("Straßenbahn Monatskarte") == "transport"
    led = Ledger()
    led.add(date(2026, 12, 2), -4900, "STRASSENBAHN")
    r = import_csv(led, "date,amount,payee\n2026-12-02,-49.00,Straßenbahn\n")
    assert (r.added, r.skipped) == (0, 1)


RULES = """# household rules
rewe => groceries
REWE  to go => takeaway
to go => takeaway-generic
amazon => shopping
amazon prime => subscriptions
prime => video
"""


def test_longest_then_first_rule():
    r = Rules.parse(RULES)
    assert r.match("REWE To Go Hbf") == "takeaway"
    assert r.match("Amazon Prime*2X4") == "subscriptions"
    assert r.match("Prime Video") == "video"
    assert Rules.parse("abc => first\nbcd => second\nxabc => longer-later\n").match("abcd") == "first"
    assert Rules.parse("bcd => second\nabc => first\n").match("abcd") == "second"
    assert Rules.parse("ab cd => a\nab  cd => b\n").match("AB CD") == "a"
    assert Rules.parse("kino => fun\ncine => film\n").match("Cinema Kino") == "fun"


# ---- calendar and ledger
import calendar
from datetime import date
from _pk import o_occurrences, rng
from potkeeper import store
from potkeeper.budget import Budget, month_status
from potkeeper.dates import add_months, next_month
from potkeeper.ledger import Ledger
from potkeeper.recurring import Recurring, occurrences


def test_add_months_every_month():
    for y in (2026, 2027, 2028):
        for m in range(1, 13):
            for day in (1, 28, 30, 31):
                if day > calendar.monthrange(y, m)[1]:
                    continue
                for k in range(0, 40):
                    yy, mm = divmod(y * 12 + m - 1 + k, 12)
                    mm += 1
                    want = date(yy, mm, min(day, calendar.monthrange(yy, mm)[1]))
                    assert add_months(date(y, m, day), k) == want, (y, m, day, k)
    assert next_month("2026-11") == "2026-12" and next_month("2026-12") == "2027-01"


def test_rent_through_december():
    r = Recurring(date(2026, 1, 31), "monthly", -95000, "Rent")
    got = occurrences(r, date(2026, 1, 1), date(2027, 2, 28))
    assert [(d.month, d.day) for d in got] == [(1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31),
                                               (8, 31), (9, 30), (10, 31), (11, 30), (12, 31), (1, 31), (2, 28)]


def test_random_occurrences():
    r = rng(21)
    for _ in range(400):
        y, m = r.randint(2023, 2027), r.randint(1, 12)
        start = date(y, m, min(r.choice([1, 15, 28, 29, 30, 31, 31]), calendar.monthrange(y, m)[1]))
        every = r.choice(["weekly", "monthly", "monthly", "yearly"])
        n = r.randint(1, 4)
        frm = date(r.randint(2023, 2028), r.randint(1, 12), 1)
        to = date(frm.year + r.randint(0, 5), r.randint(1, 12), 28)
        until = r.choice([None, date(r.randint(2025, 2031), 6, 30)])
        rule = Recurring(start, every, -1, "x", interval=n, until=until)
        assert occurrences(rule, frm, to) == o_occurrences(start, every, n, frm, to, until), rule


def test_budget_over_new_year():
    led = Ledger()
    led.add(date(2026, 11, 20), -50000, "shop", category="groceries")
    led.add(date(2027, 1, 3), -10000, "shop", category="groceries")
    b = Budget("groceries", 40000, "2026-11", cap_months=2)
    assert month_status(led, b, "2026-12") == {"available": 30000, "spent": 0, "left": 30000, "carry_out": 30000}
    assert month_status(led, b, "2027-01") == {"available": 70000, "spent": 10000, "left": 60000, "carry_out": 60000}


def test_until_inclusive():
    r = Recurring(date(2026, 1, 15), "monthly", -999, "Gym", until=date(2026, 4, 15))
    assert occurrences(r, date(2026, 1, 1), date(2026, 12, 31)) == [date(2026, m, 15) for m in (1, 2, 3, 4)]
    w = Recurring(date(2026, 12, 3), "weekly", -100, "Pocket money", interval=2, until=date(2026, 12, 31))
    assert occurrences(w, date(2026, 12, 1), date(2027, 1, 31)) == [date(2026, 12, 3), date(2026, 12, 17), date(2026, 12, 31)]


def test_ids_never_reused():
    led = Ledger()
    for i in range(3):
        led.add(date(2026, 5, 1 + i), -100 * (i + 1), "x")
    led.remove(3)
    t = led.add(date(2026, 5, 9), -1, "y")
    assert t.id == 4
    led.remove(4)
    led.remove(2)
    assert led.add(date(2026, 5, 9), -2, "z").id == 5


def test_next_id_survives_save(tmp_path):
    p = str(tmp_path / "ledger.json")
    led = Ledger()
    for i in range(4):
        led.add(date(2026, 6, 1), -1, "x")
    led.remove(4)
    led.remove(3)
    store.save_ledger(led, p)
    again = store.load_ledger(p)
    assert again.add(date(2026, 6, 2), -5, "y").id == 5
    store.save_ledger(again, p)
    assert store.load_ledger(p).add(date(2026, 6, 3), -5, "z").id == 6


def test_old_file_without_next_id(tmp_path):
    p = tmp_path / "old.json"
    p.write_text('{"version": 1, "transactions": [{"id": 7, "date": "2026-01-02", "cents": -5, "payee": "a"}]}',
                 encoding="utf-8")
    assert store.load_ledger(str(p)).add(date(2026, 1, 3), -1, "b").id == 8
    assert store.load_ledger(str(tmp_path / "missing.json")).add(date(2026, 1, 3), -1, "b").id == 1


def test_between_inclusive():
    led = Ledger()
    for d in (date(2026, 3, 1), date(2026, 3, 15), date(2026, 3, 31), date(2026, 4, 1)):
        led.add(d, -1, "x")
    assert [t.id for t in led.between(date(2026, 3, 1), date(2026, 3, 31))] == [1, 2, 3]
    assert [t.id for t in led.between(date(2026, 3, 31), date(2026, 3, 31))] == [3]


def test_old_file_keyed_by_id(tmp_path):
    rows = {str(i): {"date": "2025-12-%02d" % i, "cents": -100 * i, "payee": "p%d" % i} for i in range(1, 13)}
    del rows["7"]
    p = tmp_path / "v1.json"
    p.write_text(__import__("json").dumps({"version": 1, "transactions": rows}), encoding="utf-8")
    led = store.load_ledger(str(p))
    assert sorted(t.id for t in led) == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
    t = led.add(date(2026, 1, 2), -1, "new")
    assert t.id == 13 and len({x.id for x in led}) == 12


# ---- added for 3.10: whitespace runs, yearly 29 February


def test_payee_whitespace_runs():
    assert normalise_payee("Bakery\tHolm") == "bakery holm"
    assert normalise_payee(" Bakery \u00a0 Holm\n") == "bakery holm"
    assert normalise_payee("Bakery\u00a0Holm") == "bakery holm"
    assert Rules.parse("bakery holm => food\n").match("BAKERY\u00a0HOLM 0412") == "food"
    led = Ledger()
    led.add(date(2026, 12, 6), -350, "Bakery Holm")
    r = import_csv(led, 'date,amount,payee\n2026-12-06,-3.50,"Bakery\tHolm"\n')
    assert (r.added, r.skipped) == (0, 1)


def test_yearly_29_february():
    from potkeeper.recurring import materialise
    r = Recurring(date(2024, 2, 29), "yearly", -5000, "Leap club")
    assert occurrences(r, date(2024, 1, 1), date(2032, 12, 31)) == [
        date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29),
        date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)]
    r2 = Recurring(date(2024, 2, 29), "yearly", -1, "x", interval=2, until=date(2032, 2, 29))
    assert occurrences(r2, date(2027, 1, 1), date(2040, 1, 1)) == [date(2028, 2, 29), date(2030, 2, 28),
                                                                   date(2032, 2, 29)]
    led = Ledger()
    assert materialise(led, r, date(2028, 1, 1), date(2028, 12, 31)) == 1
    assert led.get(1).date == date(2028, 2, 29)
