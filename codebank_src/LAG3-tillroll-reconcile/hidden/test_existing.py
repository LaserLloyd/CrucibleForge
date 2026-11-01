from datetime import date

from tillroll import csvio, money, vat, zreport
from tillroll.models import Receipt
from tillroll.reconcile import Result, card_fee


def test_money_and_fee():
    assert money.parse("12") == 1200 and money.parse("0.5") == 50 and money.fmt(-123456) == "-1,234.56"
    assert [card_fee(a) for a in (1, 357, 358, 1000, 1035, 1036, 0, -100)] == [5, 5, 5, 14, 14, 15, 0, 0]


def test_csv_round_trip():
    bank = csvio.read_bank("id;date;amount;memo\n7;14.03.2026;1.234,50;CARD 0042\n8;2026-03-15;-3.00;\n")
    assert [(b.id, b.day, b.amount, b.memo, b.is_card) for b in bank] == [
        (7, date(2026, 3, 14), 123450, "CARD 0042", True), (8, date(2026, 3, 15), -300, "", False)]
    rec = csvio.read_receipts("id,date,amount,till\n1,2026-03-14,4.50,\n2,2026-03-14,1.00,back\n")
    assert rec == [Receipt(1, date(2026, 3, 14), 450), Receipt(2, date(2026, 3, 14), 100, "back")]
    out = csvio.write_matches(Result(singles=[(7, 1)], pairs=[(8, 2, 3)]))
    assert out == "kind,bank_id,receipt_ids\nsingle,7,1\npair,8,2+3\n"


def test_vat_and_z():
    s = vat.summary([(date(2026, 2, 1), 107, "reduced"), (date(2026, 4, 1), 119, "standard")])
    assert s["2026-Q1"]["reduced"] == {"gross": 107, "tax": 7, "net": 100}
    z = zreport.parse("Z 12 2026-03-14 till=back\nSALES 3 10.00\nREFUND 1 -1.00\nCASH 4.00\nCARD 5.00\n")
    assert z.till == "back" and z.balanced()
