from pulsecount.db import connect
from pulsecount.store import bump, get, set_note


def test_one_row_and_count(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    for i in range(5):
        bump(conn, "2026-10-03", "signup", 2, f"2026-10-03T0{i}:00:00Z")
    assert bump(conn, "2026-10-03", "signup", 1, "2026-10-03T06:00:00Z") == 11
    n = conn.execute("SELECT COUNT(*) FROM counters WHERE day='2026-10-03' AND name='signup'").fetchone()[0]
    assert n == 1


def test_note_and_first_seen_survive(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d1", "backup-fail", 1, "2026-10-03T01:00:00Z")
    set_note(conn, "d1", "backup-fail", "disk swapped, ignore")
    bump(conn, "d1", "backup-fail", 1, "2026-10-03T02:00:00Z")
    row = get(conn, "d1", "backup-fail")
    assert row["note"] == "disk swapped, ignore"
    assert row["first_seen"] == "2026-10-03T01:00:00Z" and row["last_seen"] == "2026-10-03T02:00:00Z"
    assert row["count"] == 2


def test_late_events_keep_earliest_and_latest(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d", "x", 1, "2026-10-03T10:00:00Z")
    bump(conn, "d", "x", 1, "2026-10-03T12:00:00Z")
    bump(conn, "d", "x", 1, "2026-10-03T09:00:00Z")      # arrived late
    bump(conn, "d", "x", 1, "2026-10-03T11:00:00Z")      # arrived late
    row = get(conn, "d", "x")
    assert (row["first_seen"], row["last_seen"], row["count"]) == ("2026-10-03T09:00:00Z", "2026-10-03T12:00:00Z", 4)


def test_id_stable_across_bumps(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d", "a", 1, "t1")
    first = get(conn, "d", "a")["id"]
    bump(conn, "d", "b", 1, "t1")
    bump(conn, "d", "a", 1, "t2")
    assert get(conn, "d", "a")["id"] == first
