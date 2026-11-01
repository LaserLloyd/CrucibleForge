# shop database: migration 4

Write `migrations/0004_price_cents.sql`, which takes a release-3 database
(`schema_v3.sql`, `PRAGMA user_version = 3`, real customer data inside) to
version 4.

How it runs: the migration runner opens the database in autocommit mode,
runs `PRAGMA foreign_keys = ON` (the app always runs with foreign keys on),
then calls `executescript()` on your file once. SQLite is 3.26 or newer.

Version 4, `products` table:

| column      | type    | constraints                                   |
|-------------|---------|-----------------------------------------------|
| id          | INTEGER | PRIMARY KEY (ids are kept)                    |
| name        | TEXT    | NOT NULL                                      |
| price_cents | INTEGER | NOT NULL, CHECK (price_cents >= 0)            |
| sku         | TEXT    | NOT NULL                                      |
| currency    | TEXT    | NOT NULL DEFAULT 'EUR'                        |

* `price_cents` = the old `price` rounded to the nearest cent (19.99 -> 1999).
* A missing (NULL) `sku` becomes `'SKU-' || id`.
* Existing rows get currency 'EUR'.
* Everything that depended on `products` keeps working: the unique index
  `products_sku`, the trigger `products_name_nonempty`, `order_items`'
  foreign key to `products(id)` (ON DELETE RESTRICT, still enforced), and the
  view `product_prices`, which now has the columns `id, name, price_cents`.
* No leftover tables; `PRAGMA foreign_key_check` finds nothing; the script
  ends with `PRAGMA user_version = 4`.
