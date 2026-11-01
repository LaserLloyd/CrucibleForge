from pulsecount.db import connect
from pulsecount.store import bump, get, set_note, top


def test_top_and_get(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    for name, n in (("b", 5), ("a", 5), ("c", 9), ("d", 1)):
        bump(conn, "d", name, n, "2026-10-03T00:00:00Z")
    assert top(conn, "d", 3) == [("c", 9), ("a", 5), ("b", 5)]
    assert top(conn, "e") == []
    assert get(conn, "d", "zz") is None


def test_note_on_single_row(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d", "a", 1, "t")
    set_note(conn, "d", "a", "hello")
    assert get(conn, "d", "a")["note"] == "hello"


def test_row_factory(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    bump(conn, "d", "a", 3, "t")
    r = get(conn, "d", "a")
    assert r["count"] == 3 and r["day"] == "d"
