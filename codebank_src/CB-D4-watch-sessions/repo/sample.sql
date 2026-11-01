INSERT INTO users VALUES (1, 'Ana', 540), (2, 'Ben', -300), (3, 'Cy', 0);
INSERT INTO plays VALUES
  (1, 1, '2026-03-01 14:00:00', 1800),   -- Ana 23:00-23:30 local
  (2, 1, '2026-03-01 14:50:00', 600),    -- 23:50-00:00: gap 20 min, same session
  (3, 1, '2026-03-01 15:40:00', 1200),   -- 00:40: gap 40 min, new session on 03-02
  (4, 2, '2026-03-02 03:00:00', 3600),   -- Ben 22:00-23:00 local 03-01
  (5, 2, '2026-03-02 03:30:00', 3600),   -- overlaps: 22:30-23:30
  (6, 2, '2026-03-02 05:00:00', 60);     -- 00:00 local 03-02, gap 30 min exactly: same session
