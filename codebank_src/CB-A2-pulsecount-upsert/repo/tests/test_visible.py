from pulsecount.db import connect
from pulsecount.store import bump, get, top


def test_first_bump(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    assert bump(conn, "2026-10-03", "signup", 1, "2026-10-03T08:00:00Z") == 1
    row = get(conn, "2026-10-03", "signup")
    assert row["first_seen"] == "2026-10-03T08:00:00Z"


def test_top_orders_by_count_then_name(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d", "b", 5, "t")
    bump(conn, "d", "a", 5, "t")
    bump(conn, "d", "c", 9, "t")
    assert top(conn, "d", 2) == [("c", 9), ("a", 5)]
