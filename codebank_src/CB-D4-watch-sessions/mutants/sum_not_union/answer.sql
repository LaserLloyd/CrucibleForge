WITH RECURSIVE
p AS (
    SELECT pl.user_id, u.name, u.utc_offset_minutes AS off,
           CAST(strftime('%s', pl.started_at) AS INTEGER) AS s,
           CAST(strftime('%s', pl.started_at) AS INTEGER) + pl.seconds AS e,
           ROW_NUMBER() OVER (PARTITION BY pl.user_id ORDER BY pl.started_at, pl.id) AS rn
    FROM plays pl JOIN users u ON u.id = pl.user_id
),
walk(user_id, rn, s, e, sess, sess_end) AS (
    SELECT user_id, rn, s, e, 1, e FROM p WHERE rn = 1
    UNION ALL
    SELECT p.user_id, p.rn, p.s, p.e,
           CASE WHEN p.s <= w.sess_end + 1800 THEN w.sess ELSE w.sess + 1 END,
           CASE WHEN p.s <= w.sess_end + 1800 THEN MAX(w.sess_end, p.e) ELSE p.e END
    FROM walk w JOIN p ON p.user_id = w.user_id AND p.rn = w.rn + 1
),
-- union length per session: walk again inside each session, merging overlaps only
cov(user_id, sess, rn, s, e, cur_end, covered) AS (
    SELECT w.user_id, w.sess, w.rn, w.s, w.e, w.e, w.e - w.s
    FROM walk w
    WHERE w.rn = (SELECT MIN(rn) FROM walk x WHERE x.user_id = w.user_id AND x.sess = w.sess)
    UNION ALL
    SELECT w.user_id, w.sess, w.rn, w.s, w.e, MAX(c.cur_end, w.e),
           c.covered + (w.e - w.s)
    FROM cov c JOIN walk w ON w.user_id = c.user_id AND w.sess = c.sess AND w.rn = c.rn + 1
),
sessions AS (
    SELECT w.user_id, w.sess, MIN(w.s) AS first_s, MAX(w.e) AS last_e,
           (SELECT covered FROM cov c WHERE c.user_id = w.user_id AND c.sess = w.sess
            ORDER BY c.rn DESC LIMIT 1) AS covered
    FROM walk w GROUP BY w.user_id, w.sess
)
SELECT u.name AS name,
       date(se.first_s + u.utc_offset_minutes * 60, 'unixepoch') AS local_date,
       COUNT(*) AS sessions,
       ROUND(SUM(se.covered) / 60.0, 1) AS total_minutes,
       ROUND(MAX(se.last_e - se.first_s) / 60.0, 1) AS longest_minutes
FROM sessions se JOIN users u ON u.id = se.user_id
GROUP BY u.id, local_date
ORDER BY name, local_date;
