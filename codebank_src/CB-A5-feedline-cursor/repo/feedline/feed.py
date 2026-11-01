"""Paging through the feed."""
import sqlite3

from .cursor import decode_cursor, encode_cursor


def page(conn: sqlite3.Connection, limit: int, cursor: str | None = None):
    """One page of items, newest first. Returns (items, next_cursor)."""
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if cursor is None:
        rows = conn.execute("SELECT * FROM items ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    else:
        before = decode_cursor(cursor)
        rows = conn.execute("SELECT * FROM items WHERE created_at < ? ORDER BY created_at DESC LIMIT ?",
                            (before, limit)).fetchall()
    items = [dict(r) for r in rows]
    next_cursor = encode_cursor(items[-1]["created_at"]) if len(items) == limit else None
    return items, next_cursor
