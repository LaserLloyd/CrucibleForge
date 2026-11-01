CREATE TABLE categories (
    id        INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES categories(id),   -- NULL for a top-level category
    name      TEXT NOT NULL
);
CREATE TABLE budgets (
    category_id INTEGER NOT NULL REFERENCES categories(id),
    month       TEXT NOT NULL,                     -- 'YYYY-MM'
    cents       INTEGER NOT NULL,                  -- > 0
    PRIMARY KEY (category_id, month)
);
CREATE TABLE spend (
    id          INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    at          TEXT NOT NULL,                     -- 'YYYY-MM-DD'
    cents       INTEGER NOT NULL                   -- negative = refund
);
