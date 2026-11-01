import pytest
from datetime import date
from _pk import o_split, rng
from potkeeper.budget import Budget, month_status
from potkeeper.export import to_csv
from potkeeper.goals import Goal, monthly_needed
from potkeeper.ledger import Ledger
from potkeeper.money import format_cents, parse_amount
from potkeeper.reports import month_report
from potkeeper.split import split_cents


def test_whole_month_and_order():
    led = Ledger()
    led.add(date(2026, 11, 30), -100, "x", category="zoo")
    led.add(date(2026, 12, 1), -700, "x", category="kids")
    led.add(date(2026, 12, 15), -500, "x", category="books")
    led.add(date(2026, 12, 15), 250000, "salary", category="income")
    led.add(date(2026, 12, 31), -200, "x", category="art")
    led.add(date(2026, 12, 31), -500, "x")
    led.add(date(2026, 12, 31), -500, "x", category="cafe")
    led.add(date(2026, 12, 20), 200, "refund", category="kids")
    led.add(date(2027, 1, 1), -9999, "x", category="kids")
    r = month_report(led, "2026-12")
    assert r["income"] == 250200
    assert r["spending"] == 2400
    assert r["by_category"] == [("books", 500), ("cafe", 500), ("kids", 500), ("uncategorised", 500),
                                ("art", 200), ("income", -250000)]


def test_february_leap():
    led = Ledger()
    led.add(date(2028, 2, 29), -100, "x", category="a")
    assert month_report(led, "2028-02")["spending"] == 100


def test_small_negative_amounts_keep_their_sign():
    assert [format_cents(c) for c in (-5, -99, -100, -101, -123450, 0, 7)] == \
        ["-0.05", "-0.99", "-1.00", "-1.01", "-1,234.50", "0.00", "0.07"]
    for c in range(-25000, 25000, 7):
        assert parse_amount(format_cents(c)) == c


def test_csv_export_exact():
    led = Ledger()
    led.add(date(2026, 8, 1), -45, "Parking")
    led.add(date(2026, 8, 2), 123456, "Salary, ACME", category="income", memo="Aug")
    assert to_csv(led) == ("id,date,amount,payee,category,memo\n1,2026-08-01,-0.45,Parking,,\n"
                           '2,2026-08-02,1234.56,"Salary, ACME",income,Aug\n')


def _led(rows):
    led = Ledger()
    for d, c in rows:
        led.add(d, c, "shop", category="groceries")
    return led


def test_overspend_carries_in_full():
    led = _led([(date(2026, 3, 10), -50000), (date(2026, 4, 2), -10000)])
    b = Budget("groceries", 40000, "2026-03")
    assert month_status(led, b, "2026-03") == {"available": 40000, "spent": 50000, "left": -10000, "carry_out": -10000}
    assert month_status(led, b, "2026-04") == {"available": 30000, "spent": 10000, "left": 20000, "carry_out": 20000}


def test_cap_months():
    led = _led([(date(2026, 1, 20), -1000), (date(2026, 2, 27), 500)])
    b = Budget("groceries", 10000, "2026-01", cap_months=3)
    assert month_status(led, b, "2026-02") == {"available": 19000, "spent": -500, "left": 19500, "carry_out": 19500}
    s = month_status(led, b, "2026-05")
    assert s["available"] == 40000 and s["carry_out"] == 30000
    nb = Budget("groceries", 10000, "2026-01", rollover=False)
    assert month_status(led, nb, "2026-02")["available"] == 10000


def test_goal_months_inclusive():
    g = Goal("bike", 50000, "2026-12", saved=10001)
    assert monthly_needed(g, "2026-09") == 10000
    assert monthly_needed(g, "2026-12") == 39999
    assert monthly_needed(g, "2027-01") == 39999
    assert monthly_needed(Goal("y", 100, "2026-03"), "2026-01") == 34
    assert monthly_needed(Goal("z", 1200, "2027-02", saved=1199), "2026-03") == 1
    assert monthly_needed(Goal("w", 99, "2026-03"), "2026-01") == 33
    assert monthly_needed(Goal("v", 3500, "2026-10"), "2026-01") == 350
    assert monthly_needed(Goal("u", 3501, "2026-10"), "2026-01") == 351


def test_split_ties_in_household_order():
    assert split_cents(100, [1, 1, 1]) == [34, 33, 33]
    assert split_cents(5, [1, 1, 1, 1]) == [2, 1, 1, 1]
    assert split_cents(7, [2, 1, 2, 1]) == [3, 1, 2, 1]


def test_refund_mirrors_expense():
    assert split_cents(-100, [1, 1, 1]) == [-34, -33, -33]
    for total, w in ((1001, [2, 1, 1]), (95000, [3, 2, 0, 2]), (1, [1, 1])):
        assert split_cents(-total, w) == [-s for s in split_cents(total, w)]


def test_random_split():
    r = rng(31)
    for _ in range(5000):
        w = [r.choice([0, 1, 1, 2, 3, 5]) for _ in range(r.randint(1, 6))]
        if sum(w) == 0:
            w[r.randrange(len(w))] = 1
        total = r.choice([r.randint(-50, 50), r.randint(-10**6, 10**6)])
        got = split_cents(total, w)
        assert got == o_split(total, w), (total, w)
        assert sum(got) == total


def test_category_netting_to_zero_is_listed():
    led = Ledger()
    led.add(date(2026, 12, 2), -2500, "Toy shop", category="toys")
    led.add(date(2026, 12, 9), 2500, "Toy shop", category="toys", memo="returned")
    led.add(date(2026, 12, 9), -100, "x", category="cafe")
    led.add(date(2026, 12, 10), 100, "x")
    assert month_report(led, "2026-12")["by_category"] == [("cafe", 100), ("toys", 0), ("uncategorised", -100)]



def test_thousands_groups_strict():
    for bad in ["1234,567.00", "1,234567.00", "-12,345678", "(1234,567)", "12,34", "1,2345", ",123", "1,,234"]:
        with pytest.raises(ValueError):
            parse_amount(bad)
    assert parse_amount("1234567") == 123456700 and parse_amount("-1,234,567.8") == -123456780
    assert parse_amount("(999,999.99)") == -99999999
