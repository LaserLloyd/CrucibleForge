"""Savings goals (README "Savings goals")."""
from dataclasses import dataclass

from .dates import parse_month


@dataclass
class Goal:
    name: str
    target: int
    due: str
    saved: int = 0

    def __post_init__(self):
        if self.target < 0 or self.saved < 0:
            raise ValueError("target and saved must be >= 0")
        parse_month(self.due)


def months_between(frm: str, to: str) -> int:
    """Number of months from frm to to, both inclusive (0 when to is before frm)."""
    fy, fm = parse_month(frm)
    ty, tm = parse_month(to)
    return max(0, (ty * 12 + tm) - (fy * 12 + fm) + 1)


def monthly_needed(goal: Goal, month: str) -> int:
    remaining = goal.target - goal.saved
    if remaining <= 0:
        return 0
    n = months_between(month, goal.due)
    if n == 0:
        return remaining
    return round(remaining / n + 0.5)             # round up to the cent


def from_json(d: dict) -> Goal:
    return Goal(d["name"], int(d["target"]), d["due"], int(d.get("saved", 0)))
