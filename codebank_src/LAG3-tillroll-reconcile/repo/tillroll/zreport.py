"""End-of-day Z reports from the tills. A report is plain text:

    Z 0417  2026-03-14  till=front
    SALES   123  1,234.50
    REFUND    2    -12.00
    VOID      1      0.00
    CASH          612.40
    CARD          610.10

Lines may be in any order; unknown lines are ignored."""
from dataclasses import dataclass
from datetime import date

from . import money


@dataclass
class ZReport:
    number: int
    day: date
    till: str
    sales: int = 0
    sales_count: int = 0
    refunds: int = 0
    voids: int = 0
    cash: int = 0
    card: int = 0

    def balanced(self) -> bool:
        """Cash + card must equal sales + refunds."""
        return self.cash + self.card == self.sales + self.refunds


def parse(text: str) -> ZReport:
    rep = None
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        head = parts[0].upper()
        if head == "Z" and len(parts) >= 3:
            till = "front"
            for p in parts[3:]:
                if p.startswith("till="):
                    till = p[5:]
            rep = ZReport(int(parts[1]), date.fromisoformat(parts[2]), till)
        elif rep is None:
            continue
        elif head == "SALES" and len(parts) == 3:
            rep.sales_count, rep.sales = int(parts[1]), money.parse(parts[2])
        elif head == "REFUND" and len(parts) == 3:
            rep.refunds = money.parse(parts[2])
        elif head == "VOID" and len(parts) >= 2:
            rep.voids = int(parts[1])
        elif head == "CASH" and len(parts) == 2:
            rep.cash = money.parse(parts[1])
        elif head == "CARD" and len(parts) == 2:
            rep.card = money.parse(parts[1])
    if rep is None:
        raise ValueError("no Z header line")
    return rep
