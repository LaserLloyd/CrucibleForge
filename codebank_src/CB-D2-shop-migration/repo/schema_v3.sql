-- the shop database as release 3 leaves it (PRAGMA user_version = 3)
CREATE TABLE products (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    price REAL NOT NULL,            -- euros, e.g. 19.99
    sku   TEXT
);
CREATE UNIQUE INDEX products_sku ON products(sku);
CREATE TABLE order_items (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    qty        INTEGER NOT NULL
);
CREATE INDEX order_items_product ON order_items(product_id);
CREATE TRIGGER products_name_nonempty BEFORE INSERT ON products
WHEN NEW.name = '' BEGIN SELECT RAISE(ABORT, 'empty product name'); END;
CREATE VIEW product_prices AS SELECT id, name, price FROM products;
