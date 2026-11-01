from feedline.db import add, connect
from feedline.feed import page


def test_no_cursor_when_nothing_follows():
    conn = connect()
    for i in range(6):
        add(conn, f"2026-10-01T0{i}:00:00Z", "note", str(i))
    items, cur = page(conn, 3)
    assert len(items) == 3 and cur is not None
    items, cur = page(conn, 3, cur)
    assert len(items) == 3 and cur is None
    items, cur = page(conn, 6)
    assert len(items) == 6 and cur is None


def test_exact_fit_with_ties():
    conn = connect()
    for i in range(4):
        add(conn, "2026-10-01T00:00:00Z", "note", str(i))
    items, cur = page(conn, 2)
    items2, cur2 = page(conn, 2, cur)
    assert [i["text"] for i in items + items2] == ["3", "2", "1", "0"] and cur2 is None
