"""The two record types the shop deals with."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BankLine:
    """One line of the bank export. ``amount`` in cents, money in > 0."""
    id: int
    day: date
    amount: int
    memo: str = ""

    @property
    def is_card(self) -> bool:
        """A card-terminal payout (the terminal keeps a fee)."""
        return self.memo.startswith("CARD ")


@dataclass(frozen=True)
class Receipt:
    """One sale (amount > 0) or refund (amount < 0) rung up on a till.
    Amount 0 = a voided sale. Each till numbers its own receipts, so ids
    repeat across tills; ``ref`` ("till:id") is what reports show."""
    id: int
    day: date
    amount: int
    till: str = "front"

    @property
    def ref(self) -> str:
        return f"{self.till}:{self.id}"
