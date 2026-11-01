"""Database: connection, schema and migrations.

The schema version lives in ``PRAGMA user_version``. ``connect(path)`` opens
the file with foreign keys ON, creates the current schema in an empty file
and otherwise calls ``migrate``, which applies the steps of ``MIGRATIONS``
that the file is missing.
"""
import sqlite3

from . import quantity, viability

SCHEMA_VERSION = 3


class SchemaTooNew(RuntimeError):
    """The file was written by a newer seedbank; refuse to touch it."""


SCHEMA = """
CREATE TABLE seeds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    variety TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    amount_milli INTEGER CHECK (amount_milli IS NULL OR amount_milli >= 0),
    unit TEXT CHECK (unit IN ('seed', 'packet', 'gram')),
    approx INTEGER NOT NULL DEFAULT 0 CHECK (approx IN (0, 1)),
    harvested INTEGER,
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK ((amount_milli IS NULL) = (unit IS NULL))
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


def _v3_amounts(conn):
    """v2 -> v3: structured amounts (docs/AMOUNTS.md); the free-text quantity goes."""
    conn.execute("ALTER TABLE seeds ADD COLUMN amount_milli INTEGER "
                 "CHECK (amount_milli IS NULL OR amount_milli >= 0)")
    conn.execute("ALTER TABLE seeds ADD COLUMN unit TEXT CHECK (unit IN ('seed', 'packet', 'gram')) "
                 "CHECK ((amount_milli IS NULL) = (unit IS NULL))")
    conn.execute("ALTER TABLE seeds ADD COLUMN approx INTEGER NOT NULL DEFAULT 0 CHECK (approx IN (0, 1))")
    rows = conn.execute("SELECT id, quantity, notes FROM seeds").fetchall()
    for sid, q, notes in rows:
        q = (q or "").strip()
        if not q:
            continue
        try:
            a = quantity.parse_quantity(q)
        except quantity.QuantityError:
            tag = f"[quantity: {q}]"
            conn.execute("UPDATE seeds SET notes = ? WHERE id = ?", (tag if not notes else notes + "\n" + tag, sid))
            continue
        if a is not None:
            conn.execute("UPDATE seeds SET amount_milli = ?, unit = ?, approx = ? WHERE id = ?",
                         (a.milli, a.unit, int(a.approx), sid))
    conn.execute("""CREATE TABLE seeds_new (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, variety TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    amount_milli INTEGER CHECK (amount_milli IS NULL OR amount_milli >= 0),
    unit TEXT CHECK (unit IN ('seed', 'packet', 'gram')),
    approx INTEGER NOT NULL DEFAULT 0 CHECK (approx IN (0, 1)),
    harvested INTEGER, source TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, CHECK ((amount_milli IS NULL) = (unit IS NULL)))""")
    conn.execute("INSERT INTO seeds_new SELECT id, name, variety, family, amount_milli, unit, approx, "
                 "harvested, source, notes, created_at FROM seeds")
    conn.execute("DROP TABLE seeds")
    conn.execute("ALTER TABLE seeds_new RENAME TO seeds")
    conn.execute("CREATE INDEX idx_seeds_name ON seeds(name)")


MIGRATIONS = [
    (2, _v2_family),
    (3, _v3_amounts),
]


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _is_empty(conn):
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0


def migrate(conn):
    """Bring the file up to SCHEMA_VERSION: every pending step and the new
    user_version in ONE transaction (all or nothing)."""
    v = version(conn)
    latest = SCHEMA_VERSION
    if v > latest:
        raise SchemaTooNew(f"database is v{v}, this seedbank knows v{latest}")
    pending = [(t, step) for t, step in MIGRATIONS if t > v]
    if not pending:
        return
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for _target, step in pending:
            step(conn)
        conn.execute(f"PRAGMA user_version = {latest}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


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
