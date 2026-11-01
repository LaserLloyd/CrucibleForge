"""Database: connection, schema and migrations.

The schema version lives in ``PRAGMA user_version``. ``connect(path)`` opens
the file with foreign keys ON, creates the current schema in an empty file
and otherwise calls ``migrate``, which applies the steps of ``MIGRATIONS``
that the file is missing.
"""
import sqlite3

from . import viability

SCHEMA_VERSION = 2


class SchemaTooNew(RuntimeError):
    """The file was written by a newer seedbank; refuse to touch it."""


SCHEMA = """
CREATE TABLE seeds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    variety TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    harvested INTEGER,
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_seeds_name ON seeds(name);
CREATE TABLE sowings (
    id INTEGER PRIMARY KEY,
    seed_id INTEGER NOT NULL REFERENCES seeds(id) ON DELETE CASCADE,
    sown_on TEXT NOT NULL,
    bed TEXT NOT NULL DEFAULT '',
    count INTEGER
);
CREATE INDEX idx_sowings_seed ON sowings(seed_id);
"""


def _v2_family(conn):
    """v1 -> v2: crop family column, filled from the seed name."""
    conn.execute("ALTER TABLE seeds ADD COLUMN family TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT id, name FROM seeds").fetchall()
    for sid, name in rows:
        conn.execute("UPDATE seeds SET family = ? WHERE id = ?", (viability.family_of(name), sid))


MIGRATIONS = [
    (2, _v2_family),
]


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _is_empty(conn):
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0


def migrate(conn):
    """Bring the file up to SCHEMA_VERSION."""
    v = version(conn)
    if v > SCHEMA_VERSION:
        raise SchemaTooNew(f"database is v{v}, this seedbank knows v{SCHEMA_VERSION}")
    for target, step in MIGRATIONS:
        if target > v:
            step(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()


def init(conn):
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if _is_empty(conn):
        init(conn)
    else:
        migrate(conn)
    return conn
