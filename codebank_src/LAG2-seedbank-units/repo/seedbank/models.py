"""Records and their JSON shape."""
from dataclasses import dataclass


@dataclass
class Seed:
    id: int
    name: str
    variety: str = ""
    family: str = ""
    quantity: str = ""
    harvested: int | None = None
    source: str = ""
    notes: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(id=row["id"], name=row["name"], variety=row["variety"], family=row["family"],
                   quantity=row["quantity"], harvested=row["harvested"], source=row["source"],
                   notes=row["notes"], created_at=row["created_at"])

    def label(self):
        return f"{self.name} ({self.variety})" if self.variety else self.name

    def to_json(self):
        return {"id": self.id, "name": self.name, "variety": self.variety, "family": self.family,
                "quantity": self.quantity, "harvested": self.harvested, "source": self.source,
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
