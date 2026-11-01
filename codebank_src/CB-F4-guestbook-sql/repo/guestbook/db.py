import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    text    TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
