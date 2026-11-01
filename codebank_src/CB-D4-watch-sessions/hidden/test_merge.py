from _db import answer, make, reference

DATA = """
INSERT INTO users VALUES (1, 'Dee', 60), (2, 'Eli', 0);
INSERT INTO plays VALUES
  (10, 1, '2026-04-01 10:00:00', 600),
  (11, 1, '2026-04-01 10:05:00', 60),      -- inside the first play
  (12, 1, '2026-04-01 10:40:00', 300),     -- 30 min after 10:10 end: joins
  (13, 1, '2026-04-01 11:15:01', 300),     -- 30 min 1 s after 10:45: new session
  (14, 1, '2026-04-01 22:50:00', 1200),    -- 23:50 local, new session, same local date
  (15, 2, '2026-04-01 09:00:00', 30),
  (16, 2, '2026-04-01 09:00:00', 90);      -- same start, longer
"""


def test_nested_touching_and_same_start():
    conn = make(DATA)
    got = answer(conn)
    assert got == [("Dee", "2026-04-01", 3, 40.0, 45.0), ("Eli", "2026-04-01", 1, 1.5, 1.5)]
    assert got == reference(conn)
