"""The ledger: expenses in, balances out."""
from .money import parse_amount
from .split import split_by_weights, split_evenly


class Ledger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, int, dict[str, int]]] = []

    def add_expense(self, payer: str, amount, participants: list[str],
                    weights: dict[str, int] | None = None) -> dict[str, int]:
        """Record that `payer` paid `amount` (text or int cents) for
        `participants`, split evenly or by `weights` (keyed by participant).
        Returns the shares."""
        cents = parse_amount(amount) if isinstance(amount, str) else int(amount)
        if weights:
            shares = split_by_weights(cents, {p: weights[p] for p in participants})
        else:
            shares = split_evenly(cents, participants)
        self.entries.append((payer, cents, shares))
        return shares

    def balances(self) -> dict[str, int]:
        """Net cents per person: positive = is owed money. Sums to 0."""
        bal: dict[str, int] = {}
        for payer, cents, shares in self.entries:
            bal[payer] = bal.get(payer, 0) + cents
            for person, share in shares.items():
                bal[person] = bal.get(person, 0) - share
        return bal
