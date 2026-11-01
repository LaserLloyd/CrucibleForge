from datetime import date

from tillroll import money
from tillroll.models import BankLine, Receipt
from tillroll.reconcile import card_fee, reconcile
from tillroll.report import monthly_report

D = date.fromisoformat


def test_money():
    assert money.parse("1,234.5") == 123450 and money.parse("-3.07") == -307
    assert money.fmt(-5) == "-0.05" and money.fmt(123450) == "1,234.50"


def test_card_fee():
    assert card_fee(1000) == 14 and card_fee(100) == 5 and card_fee(-500) == 0


def test_small_month():
    bank = [BankLine(1, D("2026-03-02"), 450, "SHOP"), BankLine(2, D("2026-03-03"), 986, "CARD 0042"),
            BankLine(3, D("2026-03-04"), 700, "SHOP"), BankLine(4, D("2026-03-05"), 0, "FEE")]
    rec = [Receipt(10, D("2026-03-01"), 450), Receipt(11, D("2026-03-03"), 1000, "back"),
           Receipt(12, D("2026-03-04"), 300), Receipt(13, D("2026-03-04"), 400),
           Receipt(14, D("2026-03-05"), 0), Receipt(10, D("2026-03-01"), 250, "back"),
           Receipt(15, D("2026-03-05"), -250, "back")]
    res = reconcile(bank, rec)
    assert res.cancelled == [("back:15", "back:10")]
    assert res.singles == [(2, "back:11"), (1, "front:10")]
    assert res.pairs == [(3, "front:12", "front:13")]
    assert res.open_bank == [4] and res.open_receipts == ["front:14"]
    assert monthly_report(bank, rec, res) == (
        "2026-03 bank 4 matched 3 open 0.00 | receipts 7 open 0 0.00 voided 1 cancelled 2\n"
        "  ! 2026-03-05 #4 0.00 FEE\n")
