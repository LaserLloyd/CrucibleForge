from _db import answer, make, reference

DATA = """
INSERT INTO users VALUES (1, 'Fay', -480), (2, 'Gus', 345), (3, 'Hal', 0);
INSERT INTO plays VALUES
  (1, 1, '2026-01-01 07:30:00', 3600),     -- 23:30 Dec 31 local, runs past local midnight
  (2, 1, '2026-01-01 08:45:00', 600),      -- joins (15 min gap), stays on the 31st
  (3, 2, '2026-01-01 18:14:00', 120),      -- 23:59 local
  (4, 2, '2026-01-01 18:16:00', 60),       -- 00:01 local next day, joins the 23:59 session
  (5, 2, '2026-01-01 19:00:00', 60);       -- new session on Jan 2 local
"""


def test_local_date_of_first_start_and_absent_user():
    conn = make(DATA)
    got = answer(conn)
    assert got == [("Fay", "2025-12-31", 1, 70.0, 85.0), ("Gus", "2026-01-01", 1, 3.0, 3.0),
                   ("Gus", "2026-01-02", 1, 1.0, 1.0)]
    assert got == reference(conn)
