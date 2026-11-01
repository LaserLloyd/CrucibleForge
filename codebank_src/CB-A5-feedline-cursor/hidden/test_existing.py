import pytest

from feedline.db import add, connect
from feedline.feed import page


def test_basic_page_and_fields():
    conn = connect()
    add(conn, "2026-10-01T10:00:00Z", "photo", "a")
    add(conn, "2026-10-02T10:00:00Z", "note", "b")
    items, _ = page(conn, 5)
    assert items[0] == {"id": 2, "created_at": "2026-10-02T10:00:00Z", "kind": "note", "text": "b"}


@pytest.mark.parametrize("limit", [0, 101, -1, "5", 2.0])
def test_limits(limit):
    with pytest.raises(ValueError):
        page(connect(), limit)


def test_empty_feed():
    assert page(connect(), 10) == ([], None)
