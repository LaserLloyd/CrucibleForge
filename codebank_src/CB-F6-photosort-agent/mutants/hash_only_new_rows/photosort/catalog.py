"""The SQLite catalog of imported photos."""
import sqlite3
from pathlib import Path

from .hashing import sha256_file


def _v2_content_hash(conn, library):
    """Release 2: duplicates are found by content. Existing rows get the
    hash of their library file."""
    conn.execute("ALTER TABLE photos ADD COLUMN content_hash TEXT")
    rows = []
    if rows and library is None:
        raise ValueError("migrating this catalog needs the library path")
    for pid, dest in rows:
        p = Path(library) / dest
        h = sha256_file(p) if p.is_file() else None
        conn.execute("UPDATE photos SET content_hash = ? WHERE id = ?", (h, pid))
    conn.execute("CREATE INDEX photos_hash ON photos(content_hash)")


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
    _v2_content_hash,
]


def migrate(conn, library=None) -> int:
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    for n, step in enumerate(MIGRATIONS, start=1):
        if n > v:
            with conn:
                if callable(step):
                    step(conn, library)
                else:
                    conn.executescript(step)
                conn.execute(f"PRAGMA user_version = {n}")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def connect(path, library=None):
    """Open and migrate the catalog. `library` is the library root (needed by
    migrations that look at the files)."""
    conn = sqlite3.connect(path)
    migrate(conn, library)
    return conn


def seen(conn, content_hash) -> bool:
    return conn.execute("SELECT 1 FROM photos WHERE content_hash = ?", (content_hash,)).fetchone() is not None


def add(conn, name, size, dest, taken_utc, content_hash) -> None:
    with conn:
        conn.execute("INSERT INTO photos (name, size, dest, taken_utc, content_hash) VALUES (?, ?, ?, ?, ?)",
                     (name, size, dest, taken_utc, content_hash))
