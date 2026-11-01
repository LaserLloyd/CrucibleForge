import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_created ON items(created_at);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add(conn: sqlite3.Connection, created_at: str, kind: str, text: str) -> int:
    with conn:
        cur = conn.execute("INSERT INTO items (created_at, kind, text) VALUES (?, ?, ?)",
                           (created_at, kind, text))
    return cur.lastrowid
