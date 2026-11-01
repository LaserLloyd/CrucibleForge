"""Schema migrations. Version = PRAGMA user_version."""
import sqlite3

MIGRATIONS = [
    # 1: initial schema
    """
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
    """,
]


def version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the database. Returns the version."""
    current = version(conn)
    for number, sql in enumerate(MIGRATIONS, start=1):
        if number <= current:
            continue
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {number}")
    return version(conn)
