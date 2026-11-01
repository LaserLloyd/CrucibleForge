"""Month report and year-end report (README "Reports")."""
from collections import defaultdict

from .dates import month_range, year_range
from .rules import UNCATEGORISED
from .split import split_cents


def by_category(transactions) -> list[tuple[str, int]]:
    spent = defaultdict(int)
    for t in transactions:
        spent[t.category or UNCATEGORISED] -= t.cents
    rows = [(c, s) for c, s in spent.items() if s]       # categories that net to nothing are noise
    return sorted(rows, key=lambda kv: (kv[1], kv[0]), reverse=True)   # spent desc, then name


def month_report(ledger, month: str) -> dict:
    start, end = month_range(month)
    ts = ledger.between(start, end)
    return {"income": sum(t.cents for t in ts if t.cents > 0),
            "spending": -sum(t.cents for t in ts if t.cents < 0),
            "by_category": by_category(ts)}


def settle(names: list[str], balances: list[int]) -> list[tuple[str, str, int]]:
    """Transfers that zero every balance: most negative pays most positive."""
    bal = list(balances)
    out = []
    while True:
        debtor = min(range(len(bal)), key=lambda i: (bal[i], i))      # most negative, earliest
        creditor = max(range(len(bal)), key=lambda i: (bal[i], i))    # most positive
        if bal[debtor] >= 0 or bal[creditor] <= 0:
            break
        amount = min(-bal[debtor], bal[creditor])
        out.append((names[debtor], names[creditor], amount))
        bal[debtor] += amount
        bal[creditor] -= amount
    return out


def year_end(ledger, household, year: int) -> dict:
    start, end = year_range(year)
    ts = ledger.between(start, end)
    names, weights = household.names, household.weights
    paid = [0] * len(names)
    share = [0] * len(names)
    for t in ts:
        if not t.shared:
            continue
        if t.paid_by not in names:
            raise ValueError(f"transaction {t.id}: paid_by {t.paid_by!r} is not a member")
        paid[names.index(t.paid_by)] -= t.cents
        for i, part in enumerate(split_cents(-t.cents, weights)):
            share[i] += part
    balance = [p - s for p, s in zip(paid, share)]
    return {"members": [{"name": n, "paid": p, "share": s, "balance": b}
                        for n, p, s, b in zip(names, paid, share, balance)],
            "settle": settle(names, balance),
            "by_category": by_category(ts)}
