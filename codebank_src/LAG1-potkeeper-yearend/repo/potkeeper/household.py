"""Members of the household and their split weights."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Member:
    name: str
    weight: int = 1
    adult: bool = True


class Household:
    def __init__(self, members):
        self.members = list(members)
        names = [m.name for m in self.members]
        if len(set(names)) != len(names):
            raise ValueError("duplicate member names")
        if not any(m.weight > 0 for m in self.members):
            raise ValueError("at least one member needs a positive weight")

    @classmethod
    def from_json(cls, d: dict) -> "Household":
        return cls(Member(m["name"], int(m.get("weight", 1)), bool(m.get("adult", True)))
                   for m in d["members"])

    def to_json(self) -> dict:
        return {"members": [{"name": m.name, "weight": m.weight, "adult": m.adult} for m in self.members]}

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.members]

    @property
    def weights(self) -> list[int]:
        return [m.weight for m in self.members]

    def index(self, name: str) -> int:
        return self.names.index(name)
