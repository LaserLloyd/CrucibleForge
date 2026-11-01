"""Transactions and the ledger."""
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Transaction:
    id: int
    date: date
    cents: int
    payee: str
    memo: str = ""
    category: str | None = None
    shared: bool = False
    paid_by: str | None = None
    tags: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Transaction":
        d = dict(d)
        d["date"] = date.fromisoformat(d["date"])
        return cls(**d)


class Ledger:
    def __init__(self, transactions=None, next_id=None):
        self.transactions: list[Transaction] = list(transactions or [])
        if next_id is None:
            next_id = max((t.id for t in self.transactions), default=0) + 1
        self._next = next_id

    def next_id(self) -> int:
        """The id the next added transaction gets (ids are never reused)."""
        return self._next

    def add(self, d: date, cents: int, payee: str, **kw) -> Transaction:
        if not isinstance(cents, int) or isinstance(cents, bool):
            raise TypeError("cents must be an int")
        t = Transaction(self._next, d, cents, payee, **kw)
        self._next += 1
        self.transactions.append(t)
        return t

    def get(self, tid: int) -> Transaction:
        for t in self.transactions:
            if t.id == tid:
                return t
        raise KeyError(tid)

    def remove(self, tid: int) -> None:
        self.transactions.remove(self.get(tid))

    def between(self, frm: date, to: date) -> list[Transaction]:
        """Transactions dated from frm to to, in ledger order."""
        return [t for t in self.transactions if frm <= t.date <= to]

    def key_counts(self, normalise) -> Counter:
        return Counter((t.date, t.cents, normalise(t.payee)) for t in self.transactions)

    def __len__(self):
        return len(self.transactions)

    def __iter__(self):
        return iter(self.transactions)
