import base64

import pytest

from _seed import seeded
from feedline.feed import page


def test_release1_cursor_still_works():
    conn = seeded()
    old = base64.urlsafe_b64encode(b"2026-10-02T12:00:00Z").decode()
    items, nxt = page(conn, 100, old)
    want = [r["id"] for r in conn.execute(
        "SELECT id FROM items WHERE created_at < '2026-10-02T12:00:00Z' ORDER BY created_at DESC, id DESC")]
    assert [i["id"] for i in items] == want and nxt is None


def test_release1_cursor_then_new_cursors():
    conn = seeded()
    old = base64.urlsafe_b64encode(b"2026-10-03T08:00:00Z").decode()
    want = [r["id"] for r in conn.execute(
        "SELECT id FROM items WHERE created_at < '2026-10-03T08:00:00Z' ORDER BY created_at DESC, id DESC")]
    got, cur = [], old
    while True:
        items, cur = page(conn, 3, cur)
        got += [i["id"] for i in items]
        if cur is None:
            break
    assert got == want


@pytest.mark.parametrize("bad", ["!!!not-base64!!!", base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode(), "%%%%"])
def test_bad_cursor(bad):
    conn = seeded(5)
    with pytest.raises(ValueError, match="bad cursor"):
        page(conn, 2, bad)


def test_cursor_is_a_string():
    conn = seeded(10)
    _, cur = page(conn, 3)
    assert isinstance(cur, str) and cur
