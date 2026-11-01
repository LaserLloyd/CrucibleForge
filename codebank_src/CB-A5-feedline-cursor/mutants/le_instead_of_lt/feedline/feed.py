"""Paging through the feed."""
import sqlite3

from .cursor import decode_cursor, encode_cursor


def page(conn: sqlite3.Connection, limit: int, cursor: str | None = None):
    """One page of items, newest first. Returns (items, next_cursor)."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if cursor is None:
        rows = conn.execute("SELECT * FROM items ORDER BY created_at DESC, id DESC LIMIT ?",
                            (limit + 1,)).fetchall()
    else:
        ts, item_id = decode_cursor(cursor)
        if item_id is None:          # release-1 cursor: strictly older than ts
            rows = conn.execute(
                "SELECT * FROM items WHERE created_at < ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (ts, limit + 1)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM items WHERE created_at < ? OR (created_at = ? AND id <= ?) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (ts, ts, item_id, limit + 1)).fetchall()
    items = [dict(r) for r in rows[:limit]]
    more = len(rows) > limit
    next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if more else None
    return items, next_cursor
