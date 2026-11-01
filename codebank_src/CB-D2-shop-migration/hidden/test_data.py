from _mig import migrated


def test_rows_converted(tmp_path):
    db = migrated(tmp_path)
    rows = db.execute("SELECT id, name, price_cents, sku, currency FROM products ORDER BY id").fetchall()
    assert rows == [(1, "Rice 5kg", 1999, "R5", "EUR"), (2, "Tea", 435, "SKU-2", "EUR"),
                    (3, "Salt", 50, "S1", "EUR"), (7, "Gift card", 10000, "SKU-7", "EUR"),
                    (9, "Freebie", 0, "FREE", "EUR")]
    assert all(type(r[2]) is int for r in rows)
    assert db.execute("SELECT count(*) FROM order_items").fetchone()[0] == 4
