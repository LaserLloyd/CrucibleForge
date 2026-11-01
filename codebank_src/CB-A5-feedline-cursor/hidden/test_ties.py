from _seed import expected, seeded, walk


def test_every_item_once_in_order():
    conn = seeded()
    want = expected(conn)
    for limit in (1, 2, 3, 7, 10, 59, 60, 100):
        assert walk(conn, limit) == want, limit


def test_items_added_at_top_while_paging():
    from feedline.db import add
    from feedline.feed import page
    conn = seeded(30, seed=9)
    want = expected(conn)
    got, cur = [], None
    items, cur = page(conn, 4)
    got += [i["id"] for i in items]
    while cur is not None:
        add(conn, "2026-10-09T00:00:00Z", "photo", "new")       # newer than everything
        items, cur = page(conn, 4, cur)
        got += [i["id"] for i in items]
    assert got == want
