import sqlite3
from pathlib import Path

DATA = """
INSERT INTO products (id, name, price, sku) VALUES
  (1, 'Rice 5kg', 19.99, 'R5'), (2, 'Tea', 4.35, NULL), (3, 'Salt', 0.5, 'S1'),
  (7, 'Gift card', 100, NULL), (9, 'Freebie', 0, 'FREE');
INSERT INTO order_items (id, order_id, product_id, qty) VALUES
  (1, 100, 1, 2), (2, 100, 2, 1), (3, 101, 7, 1), (4, 102, 1, 5);
PRAGMA user_version = 3;
"""


def migrated(tmp_path):
    path = tmp_path / "shop.db"
    setup = sqlite3.connect(path)
    setup.executescript(Path("schema_v3.sql").read_text())
    setup.executescript(DATA)
    setup.commit()
    setup.close()
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(Path("migrations/0004_price_cents.sql").read_text())
    conn.close()
    app = sqlite3.connect(path, isolation_level=None)
    app.execute("PRAGMA foreign_keys = ON")
    return app
