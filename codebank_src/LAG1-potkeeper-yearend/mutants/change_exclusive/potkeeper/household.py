"""Members of the household, their split weights and weight changes."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Member:
    name: str
    weight: int = 1
    adult: bool = True


class Household:
    def __init__(self, members, changes=()):
        self.members = list(members)
        names = [m.name for m in self.members]
        if len(set(names)) != len(names):
            raise ValueError("duplicate member names")
        if any(m.weight < 0 for m in self.members):
            raise ValueError("weights must be >= 0")
        # stable sort: same date keeps file order
        self.changes = sorted(((d, dict(w)) for d, w in changes), key=lambda c: c[0])
        for d, w in self.changes:
            for name, weight in w.items():
                if name not in names:
                    raise ValueError(f"weight change on {d}: unknown member {name!r}")
                if int(weight) < 0:
                    raise ValueError("weights must be >= 0")
        current = [m.weight for m in self.members]
        if not any(current):
            raise ValueError("at least one member needs a positive weight")
        for d, w in self.changes:
            for name, weight in w.items():
                current[names.index(name)] = int(weight)
            if not any(current):
                raise ValueError(f"weight change on {d} leaves every weight at 0")

    @classmethod
    def from_json(cls, d: dict) -> "Household":
        return cls((Member(m["name"], int(m.get("weight", 1)), bool(m.get("adult", True))) for m in d["members"]),
                   [(date.fromisoformat(c["from"]), {k: int(v) for k, v in c["weights"].items()})
                    for c in d.get("changes", [])])

    def to_json(self) -> dict:
        out = {"members": [{"name": m.name, "weight": m.weight, "adult": m.adult} for m in self.members]}
        if self.changes:
            out["changes"] = [{"from": d.isoformat(), "weights": dict(w)} for d, w in self.changes]
        return out

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.members]

    @property
    def weights(self) -> list[int]:
        return [m.weight for m in self.members]

    def weights_on(self, d: date) -> list[int]:
        current = self.weights
        names = self.names
        for frm, w in self.changes:
            if frm >= d:
                break
            for name, weight in w.items():
                current[names.index(name)] = weight
        return current

    def index(self, name: str) -> int:
        return self.names.index(name)
