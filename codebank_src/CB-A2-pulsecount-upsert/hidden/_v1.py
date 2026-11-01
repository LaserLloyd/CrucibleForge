"""Build a version-1 database exactly as the old release left it."""
import sqlite3

V1 = """
CREATE TABLE counters (
    id         INTEGER PRIMARY KEY,
    day        TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL,
    note       TEXT
);
CREATE INDEX counters_day ON counters(day);
PRAGMA user_version = 1;
"""


def make_v1(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(V1)
    conn.executemany(
        "INSERT INTO counters (id, day, name, count, first_seen, last_seen, note) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
