from datetime import date

from stmtimport import Txn, merge


def T(day, cents, payee, acct="chk"):
    return Txn(date(2026, 3, day), cents, payee, acct)


def test_payee_must_match():
    ledger = [T(2, -450, "Cafe Mondo")]
    added = merge(ledger, [T(2, -450, "Bakery Lutz")])
    assert added == [T(2, -450, "Bakery Lutz")] and len(ledger) == 2


def test_two_coffees_are_two_coffees():
    ledger = [T(2, -450, "Cafe Mondo")]
    new = [T(2, -450, "Cafe Mondo"), T(2, -450, "Cafe Mondo")]
    assert merge(ledger, new) == [T(2, -450, "Cafe Mondo")]
    assert len(ledger) == 2


def test_new_rows_do_not_absorb_each_other():
    ledger = []
    new = [T(2, -450, "Cafe Mondo"), T(3, -450, "Cafe Mondo")]
    assert merge(ledger, new) == new


def test_redated_card_payment_and_window():
    ledger = [T(5, -2999, "Shell")]
    assert merge(ledger, [T(7, -2999, "Shell")]) == []
    assert merge(ledger, [T(8, -2999, "Shell")]) == [T(8, -2999, "Shell")]
    assert merge(ledger, [T(5, -2999, "Shell", "card")]) == [T(5, -2999, "Shell", "card")]
