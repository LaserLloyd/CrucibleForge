"""Monthly category budgets with rollover (README "Budgets")."""
from dataclasses import dataclass

from .dates import month_range, next_month, parse_month


@dataclass
class Budget:
    category: str
    amount: int
    since: str
    rollover: bool = True
    cap_months: int = 1

    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("budget amount must be >= 0")
        if self.cap_months < 1:
            raise ValueError("cap_months must be >= 1")
        parse_month(self.since)


def spent_in(ledger, category: str, month: str) -> int:
    start, end = month_range(month)
    return -sum(t.cents for t in ledger.between(start, end) if t.category == category)


def carry(budget: Budget, left: int) -> int:
    if not budget.rollover:
        return 0
    return max(0, min(left, budget.amount))


def month_status(ledger, budget: Budget, month: str) -> dict:
    if parse_month(month) < parse_month(budget.since):
        raise ValueError(f"{budget.category} has no budget before {budget.since}")
    m, carry_in = budget.since, 0
    while True:
        available = budget.amount + carry_in
        spent = spent_in(ledger, budget.category, m)
        left = available - spent
        out = carry(budget, left)
        if m == month:
            return {"available": available, "spent": spent, "left": left, "carry_out": out}
        m, carry_in = next_month(m), out
