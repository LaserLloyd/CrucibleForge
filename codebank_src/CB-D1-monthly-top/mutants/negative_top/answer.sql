WITH RECURSIVE months(m) AS (
    SELECT '2026-01'
    UNION ALL
    SELECT strftime('%Y-%m', date(m || '-01', '+1 month')) FROM months WHERE m < '2026-06'
),
spend AS (
    SELECT strftime('%Y-%m', o.placed_at) AS m, o.customer_id AS cid, o.total_cents AS cents
    FROM orders o WHERE o.status IN ('paid', 'refunded')
    UNION ALL
    SELECT strftime('%Y-%m', r.refunded_at), o.customer_id, -r.amount_cents
    FROM refunds r JOIN orders o ON o.id = r.order_id
),
net AS (
    SELECT m, cid, SUM(cents) AS net FROM spend GROUP BY m, cid
),
ranked AS (
    SELECT m, cid, net, RANK() OVER (PARTITION BY m ORDER BY net DESC) AS rk FROM net
)
SELECT months.m AS month, c.name AS customer, COALESCE(r.net, 0) AS net_cents
FROM months
LEFT JOIN ranked r ON r.m = months.m AND r.rk = 1
LEFT JOIN customers c ON c.id = r.cid
ORDER BY month, customer;
