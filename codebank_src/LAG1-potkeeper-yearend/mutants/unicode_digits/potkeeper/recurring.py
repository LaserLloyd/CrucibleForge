"""Recurring transactions: rent, subscriptions, pocket money (README "Recurring")."""
from dataclasses import dataclass
from datetime import date, timedelta

from .dates import add_months
from .rules import normalise_payee

EVERY = ("weekly", "monthly", "yearly")


@dataclass
class Recurring:
    start: date
    every: str
    cents: int
    payee: str
    interval: int = 1
    until: date | None = None
    category: str | None = None
    shared: bool = False
    paid_by: str | None = None

    def __post_init__(self):
        if self.every not in EVERY:
            raise ValueError(f"every must be one of {EVERY}")
        if self.interval < 1:
            raise ValueError("interval must be >= 1")


def nth(rule: Recurring, k: int) -> date:
    """Occurrence k, always computed from the start (a month-end clamp never drifts)."""
    if rule.every == "weekly":
        return rule.start + timedelta(days=7 * rule.interval * k)
    if rule.every == "monthly":
        return add_months(rule.start, rule.interval * k)
    return add_months(rule.start, 12 * rule.interval * k)


def occurrences(rule: Recurring, frm: date, to: date) -> list[date]:
    out = []
    k, d = 0, rule.start
    while d <= to and (rule.until is None or d <= rule.until):
        if d >= frm:
            out.append(d)
        k += 1
        d = nth(rule, k)
    return out


def materialise(ledger, rule: Recurring, frm: date, to: date) -> int:
    have = ledger.key_counts(normalise_payee)
    key_payee = normalise_payee(rule.payee)
    n = 0
    for d in occurrences(rule, frm, to):
        if have[(d, rule.cents, key_payee)]:
            continue
        ledger.add(d, rule.cents, rule.payee, category=rule.category,
                   shared=rule.shared, paid_by=rule.paid_by, memo="recurring")
        n += 1
    return n
