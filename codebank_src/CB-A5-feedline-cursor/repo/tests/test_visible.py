import pytest

from feedline.db import add, connect
from feedline.feed import page


def test_first_page_newest_first():
    conn = connect()
    add(conn, "2026-10-01T10:00:00Z", "photo", "a")
    add(conn, "2026-10-02T10:00:00Z", "photo", "b")
    items, _ = page(conn, 10)
    assert [i["text"] for i in items] == ["b", "a"]


def test_limit_bounds():
    conn = connect()
    with pytest.raises(ValueError):
        page(conn, 0)
