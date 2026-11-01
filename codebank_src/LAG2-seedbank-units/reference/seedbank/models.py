"""Records and their JSON shape."""
from dataclasses import dataclass

from .quantity import Amount, format_amount, format_number


@dataclass
class Seed:
    id: int
    name: str
    variety: str = ""
    family: str = ""
    amount: Amount | None = None
    harvested: int | None = None
    source: str = ""
    notes: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(id=row["id"], name=row["name"], variety=row["variety"], family=row["family"],
                   amount=None if row["amount_milli"] is None else
                   Amount(row["amount_milli"], row["unit"], bool(row["approx"])),
                   harvested=row["harvested"], source=row["source"],
                   notes=row["notes"], created_at=row["created_at"])

    def label(self):
        return f"{self.name} ({self.variety})" if self.variety else self.name

    @property
    def quantity(self):
        return format_amount(self.amount)

    def amount_json(self):
        a = self.amount
        if a is None:
            return None
        return {"value": format_number(a.milli), "unit": a.unit, "approx": a.approx}

    def to_json(self):
        return {"id": self.id, "name": self.name, "variety": self.variety, "family": self.family,
                "amount": self.amount_json(), "quantity": self.quantity, "harvested": self.harvested, "source": self.source,
                "notes": self.notes, "created_at": self.created_at}


@dataclass
class Sowing:
    id: int
    seed_id: int
    sown_on: str
    bed: str = ""
    count: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(id=row["id"], seed_id=row["seed_id"], sown_on=row["sown_on"], bed=row["bed"],
                   count=row["count"])

    def to_json(self):
        return {"id": self.id, "seed_id": self.seed_id, "sown_on": self.sown_on, "bed": self.bed,
                "count": self.count}
