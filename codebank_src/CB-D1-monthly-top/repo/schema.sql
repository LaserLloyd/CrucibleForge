CREATE TABLE customers (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    placed_at   TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS' (UTC)
    total_cents INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'refunded'))
);
CREATE TABLE refunds (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL,      -- positive
    refunded_at  TEXT NOT NULL          -- 'YYYY-MM-DD HH:MM:SS' (UTC)
);
