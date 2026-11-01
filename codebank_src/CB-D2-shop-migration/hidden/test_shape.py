import sqlite3

import pytest

from _mig import migrated


def test_products_table(tmp_path):
    db = migrated(tmp_path)
    cols = [(r[1], r[2].upper(), r[3], r[4], r[5]) for r in db.execute("PRAGMA table_info(products)")]
    assert cols == [("id", "INTEGER", 0, None, 1), ("name", "TEXT", 1, None, 0),
                    ("price_cents", "INTEGER", 1, None, 0), ("sku", "TEXT", 1, None, 0),
                    ("currency", "TEXT", 1, "'EUR'", 0)]
    assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO products (name, price_cents, sku) VALUES ('x', -1, 'neg')")


def test_dependents_recreated(tmp_path):
    db = migrated(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO products (name, price_cents, sku) VALUES ('dup', 1, 'R5')")
    with pytest.raises(sqlite3.IntegrityError, match="empty product name"):
        db.execute("INSERT INTO products (name, price_cents, sku) VALUES ('', 1, 'E1')")
    assert db.execute("SELECT * FROM product_prices WHERE id = 1").fetchone() == (1, "Rice 5kg", 1999)
    cols = [d[0] for d in db.execute("SELECT * FROM product_prices").description]
    assert cols == ["id", "name", "price_cents"]
    idx = {r[1]: r[2] for r in db.execute("PRAGMA index_list(products)")}
    assert idx.get("products_sku") == 1
