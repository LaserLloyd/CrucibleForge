import sqlite3
from pathlib import Path

HERE = Path(__file__).parent


def make(inserts: str):
    conn = sqlite3.connect(":memory:")
    conn.executescript(Path("schema.sql").read_text())
    conn.executescript(inserts)
    return conn


def answer(conn):
    sql = Path("answer.sql").read_text()
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    assert cols == ["month", "customer", "net_cents"], cols
    return [tuple(r) for r in cur.fetchall()]


def reference(conn):
    return [tuple(r) for r in conn.execute((HERE / "_ref.sql").read_text()).fetchall()]
