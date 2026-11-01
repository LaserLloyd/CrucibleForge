"""A last-writer-wins register."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Stamp:
    lamport: int
    replica: str

    def as_list(self):
        return [self.lamport, self.replica]


def newer(a: Stamp | None, b: Stamp | None) -> bool:
    """True when stamp `a` beats stamp `b`."""
    if b is None:
        return True
    if a is None:
        return False
    return a.lamport > b.lamport


def write(reg: dict, field: str, value, stamp: Stamp) -> None:
    """Apply a write to `reg[field] = (value, stamp)` if it wins."""
    cur = reg.get(field)
    if cur is None or newer(stamp, cur[1]):
        reg[field] = (value, stamp)
