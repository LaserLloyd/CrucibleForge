from tabsplit import Ledger
from tabsplit.money import format_cents
from tabsplit.report import report
from tabsplit.settle import settle
from tabsplit.split import split_evenly


def test_format_and_settle_unchanged():
    assert format_cents(0) == "0.00" and format_cents(-123456) == "-1,234.56"
    assert settle({"a": 300, "b": -100, "c": -200}) == [("c", "a", 200), ("b", "a", 100)]


def test_exact_even_split_and_report():
    assert split_evenly(600, ["x", "y"]) == {"x": 300, "y": 300}
    led = Ledger()
    led.add_expense("ana", "30", ["ana", "ben", "cy"])
    assert report(led) == ("ana is owed 20.00\nben owes 10.00\ncy owes 10.00\n"
                           "ben -> ana: 10.00\ncy -> ana: 10.00")


def test_weighted_ledger_int_amount():
    led = Ledger()
    shares = led.add_expense("ben", 900, ["ana", "ben"], weights={"ana": 2, "ben": 1})
    assert shares == {"ana": 600, "ben": 300}
