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
    # 2: merge duplicate (day, name) rows, then enforce uniqueness
    """
    UPDATE counters SET
        count = (SELECT SUM(d.count) FROM counters d WHERE d.day = counters.day AND d.name = counters.name),
        first_seen = (SELECT MIN(d.first_seen) FROM counters d WHERE d.day = counters.day AND d.name = counters.name),
        last_seen = (SELECT MAX(d.last_seen) FROM counters d WHERE d.day = counters.day AND d.name = counters.name),
        note = (SELECT d.note FROM counters d WHERE d.day = counters.day AND d.name = counters.name
                AND d.note IS NOT NULL AND d.note <> '' ORDER BY d.id LIMIT 1)
    WHERE id = (SELECT MIN(d.id) FROM counters d WHERE d.day = counters.day AND d.name = counters.name)
      AND (SELECT COUNT(*) FROM counters d WHERE d.day = counters.day AND d.name = counters.name) > 1;
    DELETE FROM counters
    WHERE id <> (SELECT MIN(d.id) FROM counters d WHERE d.day = counters.day AND d.name = counters.name);
    CREATE UNIQUE INDEX counters_day_name ON counters(day, name);
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
