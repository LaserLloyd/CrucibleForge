PRAGMA foreign_keys = OFF;
BEGIN;
DROP VIEW product_prices;
CREATE TABLE products_new (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    sku         TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'EUR'
);
INSERT INTO products_new (id, name, price_cents, sku, currency)
    SELECT id, name, CAST(ROUND(price * 100) AS INTEGER), COALESCE(sku, ''), 'EUR' FROM products;
DROP TABLE products;
ALTER TABLE products_new RENAME TO products;
CREATE UNIQUE INDEX products_sku ON products(sku);
CREATE TRIGGER products_name_nonempty BEFORE INSERT ON products
WHEN NEW.name = '' BEGIN SELECT RAISE(ABORT, 'empty product name'); END;
CREATE VIEW product_prices AS SELECT id, name, price_cents FROM products;
PRAGMA user_version = 4;
COMMIT;
PRAGMA foreign_keys = ON;
