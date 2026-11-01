"""The SQLite catalog of imported photos."""
import sqlite3

MIGRATIONS = [
    """
    CREATE TABLE photos (
        id        INTEGER PRIMARY KEY,
        name      TEXT NOT NULL,
        size      INTEGER NOT NULL,
        dest      TEXT NOT NULL,          -- path relative to the library
        taken_utc TEXT NOT NULL
    );
    """,
]


def migrate(conn, library=None) -> int:
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    for n, sql in enumerate(MIGRATIONS, start=1):
        if n > v:
            with conn:
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {n}")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def connect(path, library=None):
    """Open and migrate the catalog. `library` is the library root (needed by
    migrations that look at the files)."""
    conn = sqlite3.connect(path)
    migrate(conn, library)
    return conn


def seen(conn, name, size) -> bool:
    return conn.execute("SELECT 1 FROM photos WHERE name = ? AND size = ?", (name, size)).fetchone() is not None


def add(conn, name, size, dest, taken_utc) -> None:
    with conn:
        conn.execute("INSERT INTO photos (name, size, dest, taken_utc) VALUES (?, ?, ?, ?)",
                     (name, size, dest, taken_utc))
