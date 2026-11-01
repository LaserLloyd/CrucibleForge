import sqlite3

import pytest

from _mig import migrated


def test_foreign_key_still_points_at_products(tmp_path):
    db = migrated(tmp_path)
    fks = db.execute("PRAGMA foreign_key_list(order_items)").fetchall()
    assert [(r[2], r[3], r[4], r[6]) for r in fks] == [("products", "product_id", "id", "RESTRICT")]
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_foreign_key_enforced(tmp_path):
    db = migrated(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO order_items (order_id, product_id, qty) VALUES (1, 999, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM products WHERE id = 1")
    db.execute("DELETE FROM products WHERE id = 9")


def test_no_leftovers(tmp_path):
    db = migrated(tmp_path)
    names = sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
    assert names == ["order_items", "products"]
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
