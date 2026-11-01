import sqlite3
from pathlib import Path

HERE = Path(__file__).parent


def make(inserts):
    conn = sqlite3.connect(":memory:")
    conn.executescript(Path("schema.sql").read_text())
    conn.executescript(inserts)
    return conn


def answer(conn):
    cur = conn.execute(Path("answer.sql").read_text())
    assert [d[0] for d in cur.description] == ["name", "local_date", "sessions", "total_minutes", "longest_minutes"]
    return [tuple(r) for r in cur.fetchall()]


def reference(conn):
    return [tuple(r) for r in conn.execute((HERE / "_ref.sql").read_text()).fetchall()]
