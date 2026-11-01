"""Plain-text report of a ledger."""
from .ledger import Ledger
from .money import format_cents
from .settle import settle


def report(ledger: Ledger) -> str:
    bal = ledger.balances()
    lines = []
    for person in sorted(bal):
        b = bal[person]
        verb = "is owed" if b >= 0 else "owes"
        lines.append(f"{person} {verb} {format_cents(abs(b))}")
    for d, c, amt in settle(bal):
        lines.append(f"{d} -> {c}: {format_cents(amt)}")
    return "\n".join(lines)
