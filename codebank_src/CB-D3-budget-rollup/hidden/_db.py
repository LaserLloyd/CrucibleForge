import sqlite3
from pathlib import Path

HERE = Path(__file__).parent


def make(inserts):
    conn = sqlite3.connect(":memory:")
    conn.executescript(Path("schema.sql").read_text())
    conn.executescript(inserts)
    return conn


def answer(conn, month):
    cur = conn.execute(Path("answer.sql").read_text(), {"month": month})
    assert [d[0] for d in cur.description] == ["path", "own_cents", "total_cents", "budget_cents",
                                                "remaining_cents", "over"]
    return [tuple(r) for r in cur.fetchall()]


def reference(conn, month):
    return [tuple(r) for r in conn.execute((HERE / "_ref.sql").read_text(), {"month": month}).fetchall()]
