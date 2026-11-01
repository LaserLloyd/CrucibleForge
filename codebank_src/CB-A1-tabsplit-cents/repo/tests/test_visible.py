from tabsplit import Ledger
from tabsplit.money import format_cents, parse_amount
from tabsplit.settle import settle
from tabsplit.split import split_evenly


def test_parse_whole_number():
    assert parse_amount("12") == 1200


def test_format_cents():
    assert format_cents(1234567) == "12,345.67"
    assert format_cents(-5) == "-0.05"


def test_even_split_exact():
    assert split_evenly(900, ["ana", "ben", "cy"]) == {"ana": 300, "ben": 300, "cy": 300}


def test_settle_two_people():
    assert settle({"ana": 500, "ben": -500}) == [("ben", "ana", 500)]


def test_ledger_simple():
    led = Ledger()
    led.add_expense("ana", "30", ["ana", "ben", "cy"])
    assert led.balances() == {"ana": 2000, "ben": -1000, "cy": -1000}
