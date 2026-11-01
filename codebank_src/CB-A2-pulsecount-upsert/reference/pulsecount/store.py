"""Reading and writing counters."""
import sqlite3


def bump(conn: sqlite3.Connection, day: str, name: str, n: int = 1, at: str = "") -> int:
    """Add `n` to the (day, name) counter; `at` is the event time (ISO UTC).
    Returns the new count."""
    with conn:
        conn.execute(
            """INSERT INTO counters (day, name, count, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (day, name) DO UPDATE SET
                   count = count + excluded.count,
                   first_seen = MIN(first_seen, excluded.first_seen),
                   last_seen = MAX(last_seen, excluded.last_seen)""",
            (day, name, n, at, at),
        )
    return get(conn, day, name)["count"]


def get(conn: sqlite3.Connection, day: str, name: str):
    """The row of (day, name), or None."""
    return conn.execute(
        "SELECT * FROM counters WHERE day = ? AND name = ? ORDER BY id", (day, name)
    ).fetchone()


def set_note(conn: sqlite3.Connection, day: str, name: str, note: str) -> None:
    with conn:
        conn.execute("UPDATE counters SET note = ? WHERE day = ? AND name = ?", (note, day, name))


def top(conn: sqlite3.Connection, day: str, k: int = 3) -> list[tuple[str, int]]:
    """The k biggest counters of a day as (name, count), biggest first, ties by name."""
    rows = conn.execute(
        "SELECT name, count FROM counters WHERE day = ? ORDER BY count DESC, name LIMIT ?",
        (day, k),
    ).fetchall()
    return [(r["name"], r["count"]) for r in rows]
