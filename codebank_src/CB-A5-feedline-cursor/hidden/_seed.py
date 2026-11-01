import random

from feedline.db import add, connect


def seeded(n=60, seed=5):
    """A feed with many shared timestamps (bulk uploads land in the same second)."""
    rnd = random.Random(seed)
    conn = connect()
    stamps = [f"2026-10-0{d}T{h:02d}:00:00Z" for d in range(1, 4) for h in (8, 12, 20)]
    for i in range(n):
        add(conn, rnd.choice(stamps), "photo", f"item{i}")
    return conn


def expected(conn):
    return [r["id"] for r in conn.execute("SELECT id FROM items ORDER BY created_at DESC, id DESC")]


def walk(conn, limit):
    out, cur, pages = [], None, 0
    while True:
        items, cur = page_(conn, limit, cur)
        out += [i["id"] for i in items]
        pages += 1
        assert pages < 500
        if cur is None:
            return out


def page_(conn, limit, cur):
    from feedline.feed import page
    return page(conn, limit, cur)
