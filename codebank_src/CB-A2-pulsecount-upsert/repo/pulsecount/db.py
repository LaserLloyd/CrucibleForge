import sqlite3

from .migrate import migrate


def connect(path: str) -> sqlite3.Connection:
    """Open (and migrate) the counters database."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn
