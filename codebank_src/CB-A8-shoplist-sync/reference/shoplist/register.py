"""A last-writer-wins register."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Stamp:
    lamport: int
    replica: str

    def as_list(self):
        return [self.lamport, self.replica]

    def key(self):
        return (self.lamport, self.replica)


def wins(field: str, value, stamp: Stamp, cur) -> bool:
    """Whether the write (value, stamp) beats the current (value, stamp) of `field`."""
    if cur is None:
        return True
    cur_value, cur_stamp = cur
    if field == "present" and stamp.lamport == cur_stamp.lamport and value != cur_value:
        return value is True
    return stamp.key() > cur_stamp.key()


def write(reg: dict, field: str, value, stamp: Stamp) -> None:
    """Apply a write to `reg[field] = (value, stamp)` if it wins."""
    if wins(field, value, stamp, reg.get(field)):
        reg[field] = (value, stamp)
