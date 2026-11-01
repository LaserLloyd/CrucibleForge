import sqlite3

from _v1 import make_v1
from pulsecount.db import connect
from pulsecount.store import bump, get

ROWS = [
    (1, "2026-10-03", "signup", 3, "2026-10-03T09:00:00Z", "2026-10-03T10:00:00Z", None),
    (2, "2026-10-03", "login", 7, "2026-10-03T08:00:00Z", "2026-10-03T08:30:00Z", "steady"),
    (3, "2026-10-03", "signup", 4, "2026-10-03T07:00:00Z", "2026-10-03T09:30:00Z", ""),
    (4, "2026-10-03", "signup", 2, "2026-10-03T11:00:00Z", "2026-10-03T12:00:00Z", "promo day"),
    (5, "2026-10-04", "signup", 1, "2026-10-04T00:10:00Z", "2026-10-04T00:10:00Z", None),
    (6, "2026-10-03", "signup", 1, "2026-10-03T08:00:00Z", "2026-10-03T08:00:00Z", "later note"),
    (7, "2026-10-03", "backup-fail", 1, "2026-10-03T02:00:00Z", "2026-10-03T02:00:00Z", None),
    (8, "2026-10-03", "backup-fail", 1, "2026-10-03T03:00:00Z", "2026-10-03T03:00:00Z", None),
]


def _rows(path):
    c = sqlite3.connect(path)
    out = c.execute("SELECT id, day, name, count, first_seen, last_seen, note FROM counters ORDER BY id").fetchall()
    c.close()
    return out


def test_merge_rules(tmp_path):
    path = str(tmp_path / "old.db")
    make_v1(path, ROWS)
    conn = connect(path)
    conn.close()
    assert _rows(path) == [
        (1, "2026-10-03", "signup", 10, "2026-10-03T07:00:00Z", "2026-10-03T12:00:00Z", "promo day"),
        (2, "2026-10-03", "login", 7, "2026-10-03T08:00:00Z", "2026-10-03T08:30:00Z", "steady"),
        (5, "2026-10-04", "signup", 1, "2026-10-04T00:10:00Z", "2026-10-04T00:10:00Z", None),
        (7, "2026-10-03", "backup-fail", 2, "2026-10-03T02:00:00Z", "2026-10-03T03:00:00Z", None),
    ]


def test_bump_after_migration(tmp_path):
    path = str(tmp_path / "old.db")
    make_v1(path, ROWS)
    conn = connect(path)
    assert bump(conn, "2026-10-03", "signup", 5, "2026-10-03T13:00:00Z") == 15
    row = get(conn, "2026-10-03", "signup")
    assert row["id"] == 1 and row["note"] == "promo day" and row["last_seen"] == "2026-10-03T13:00:00Z"
