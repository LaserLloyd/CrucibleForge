"""The pantry, stored in SQLite."""
import sqlite3

from .units import convert, normalize


class Pantry:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS stock (item TEXT PRIMARY KEY, qty REAL NOT NULL, unit TEXT NOT NULL)")

    def stock(self, item: str):
        row = self.conn.execute("SELECT qty, unit FROM stock WHERE item = ?", (item.lower(),)).fetchone()
        return (row[0], row[1]) if row else None

    def add(self, item: str, qty, unit: str) -> None:
        """Add qty of item; converts to the unit the item is already stored in."""
        unit = normalize(unit)
        have = self.stock(item)
        with self.conn:
            if have is None:
                self.conn.execute("INSERT INTO stock (item, qty, unit) VALUES (?, ?, ?)",
                                  (item.lower(), float(qty), unit))
            else:
                total = have[0] + float(qty)
                self.conn.execute("UPDATE stock SET qty = ? WHERE item = ?", (total, item.lower()))

    def use(self, item: str, qty, unit: str) -> None:
        have = self.stock(item)
        if have is None:
            raise KeyError(item)
        left = have[0] - convert(qty, unit, have[1])
        if left < 0:
            raise ValueError(f"not enough {item}")
        with self.conn:
            self.conn.execute("UPDATE stock SET qty = ? WHERE item = ?", (left, item.lower()))

    def items(self):
        return [r[0] for r in self.conn.execute("SELECT item FROM stock ORDER BY item")]
