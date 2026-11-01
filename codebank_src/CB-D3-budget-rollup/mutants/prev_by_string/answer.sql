WITH RECURSIVE
paths(id, path) AS (
    SELECT id, name FROM categories WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, p.path || ' > ' || c.name FROM categories c JOIN paths p ON c.parent_id = p.id
),
closure(ancestor, descendant) AS (
    SELECT id, id FROM categories
    UNION ALL
    SELECT cl.ancestor, c.id FROM closure cl JOIN categories c ON c.parent_id = cl.descendant
),
prev(m) AS (
    SELECT substr(:month, 1, 5) || printf('%02d', CAST(substr(:month, 6, 2) AS INTEGER) - 1)
),
own_now AS (
    SELECT category_id AS id, SUM(cents) AS cents FROM spend
    WHERE strftime('%Y-%m', at) = :month GROUP BY category_id
),
own_prev AS (
    SELECT category_id AS id, SUM(cents) AS cents FROM spend
    WHERE strftime('%Y-%m', at) = (SELECT m FROM prev) GROUP BY category_id
),
totals AS (
    SELECT cl.ancestor AS id,
           COALESCE(SUM(n.cents), 0) AS total_now
    FROM closure cl LEFT JOIN own_now n ON n.id = cl.descendant
    GROUP BY cl.ancestor
),
totals_prev AS (
    SELECT cl.ancestor AS id, COALESCE(SUM(p.cents), 0) AS total_prev
    FROM closure cl LEFT JOIN own_prev p ON p.id = cl.descendant
    GROUP BY cl.ancestor
),
budget_now AS (
    SELECT b.category_id AS id,
           b.cents + COALESCE((SELECT MAX(bp.cents - tp.total_prev, 0)
                               FROM budgets bp JOIN totals_prev tp ON tp.id = bp.category_id
                               WHERE bp.category_id = b.category_id AND bp.month = (SELECT m FROM prev)), 0) AS cents
    FROM budgets b WHERE b.month = :month
)
SELECT p.path AS path,
       COALESCE(o.cents, 0) AS own_cents,
       t.total_now AS total_cents,
       bn.cents AS budget_cents,
       bn.cents - t.total_now AS remaining_cents,
       CASE WHEN bn.cents IS NOT NULL AND t.total_now > bn.cents THEN 1 ELSE 0 END AS over
FROM paths p
JOIN totals t ON t.id = p.id
LEFT JOIN own_now o ON o.id = p.id
LEFT JOIN budget_now bn ON bn.id = p.id
ORDER BY p.path;
