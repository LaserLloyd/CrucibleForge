"""One phone's copy of the list."""
import json

from .keys import item_key
from .register import Stamp, write

DEFAULTS = {"qty": 1, "checked": False}


class Replica:
    def __init__(self, replica_id: str):
        self.id = replica_id
        self.clock = 0
        self.items_state: dict[str, dict] = {}     # key -> {field: (value, Stamp)}

    def _stamp(self) -> Stamp:
        self.clock += 1
        return Stamp(self.clock, self.id)

    def _reg(self, key: str) -> dict:
        return self.items_state.setdefault(key, {})

    def add(self, name: str, qty: int = 1) -> None:
        if qty < 1:
            raise ValueError("qty must be >= 1")
        s = self._stamp()
        reg = self._reg(item_key(name))
        write(reg, "present", True, s)
        write(reg, "name", " ".join(name.split()), s)
        write(reg, "qty", qty, s)

    def set_qty(self, name: str, qty: int) -> None:
        if qty < 1:
            raise ValueError("qty must be >= 1")
        write(self._reg(item_key(name)), "qty", qty, self._stamp())

    def check(self, name: str, checked: bool = True) -> None:
        write(self._reg(item_key(name)), "checked", checked, self._stamp())

    def uncheck(self, name: str) -> None:
        self.check(name, False)

    def remove(self, name: str) -> None:
        write(self._reg(item_key(name)), "present", False, self._stamp())

    def merge(self, other: "Replica") -> None:
        top = max(self.clock, other.clock)
        for key, reg in other.items_state.items():
            mine = self._reg(key)
            for field, (value, stamp) in reg.items():
                write(mine, field, value, stamp)
                top = max(top, stamp.lamport)
        self.clock = top

    def state(self) -> dict:
        """Canonical, comparable view of everything this replica knows."""
        return {k: {f: (v, s.as_list()) for f, (v, s) in sorted(reg.items())}
                for k, reg in sorted(self.items_state.items())}

    def items(self) -> list[tuple[str, int, bool]]:
        out = []
        for key in sorted(self.items_state):
            reg = self.items_state[key]
            if reg.get("present", (False, None))[0]:
                out.append((reg.get("name", (key, None))[0], reg.get("qty", (1, None))[0],
                            reg.get("checked", (False, None))[0]))
        return out

    def dump(self) -> str:
        return json.dumps({"id": self.id, "clock": self.clock,
                           "items": {k: {f: [v, s.as_list()] for f, (v, s) in reg.items()}
                                     for k, reg in self.items_state.items()}})

    @classmethod
    def load(cls, text: str) -> "Replica":
        d = json.loads(text)
        r = cls(d["id"])
        r.clock = d["clock"]
        r.items_state = {k: {f: (v, Stamp(s[0], s[1])) for f, (v, s) in reg.items()}
                         for k, reg in d["items"].items()}
        return r
