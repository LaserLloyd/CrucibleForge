from datetime import date

from potkeeper.household import Household, Member
from potkeeper.importer import import_csv
from potkeeper.ledger import Ledger
from potkeeper.money import format_cents, parse_amount
from potkeeper.rules import Rules
from potkeeper.split import split_cents
from potkeeper import store


def test_amounts():
    assert parse_amount("12.5") == 1250
    assert parse_amount(" -3.07 ") == -307
    assert parse_amount("(12.00)") == -1200
    assert format_cents(-123450) == "-1,234.50"


def test_import_and_rules():
    led = Ledger()
    r = import_csv(led, "date,amount,payee\n2026-12-01,-4.20,Kaffeeklatsch\n02.12.2026,2500.00,Salary ACME\n")
    assert (r.added, r.skipped) == (2, 0)
    Rules.parse("kaffee => coffee\nsalary => income\n").categorise(led)
    assert [t.category for t in led] == ["coffee", "income"]


def test_even_split():
    assert split_cents(100, [1, 1]) == [50, 50]
    assert sum(split_cents(1001, [2, 1, 1])) == 1001


def test_store_round_trip(tmp_path):
    led = Ledger()
    led.add(date(2026, 1, 2), -999, "Bakery", shared=True, paid_by="Ana")
    store.save_ledger(led, str(tmp_path / "l.json"))
    again = store.load_ledger(str(tmp_path / "l.json"))
    assert [t.to_json() for t in again] == [t.to_json() for t in led]
    hh = Household([Member("Ana", 1), Member("Ben", 1)])
    assert hh.weights == [1, 1]
