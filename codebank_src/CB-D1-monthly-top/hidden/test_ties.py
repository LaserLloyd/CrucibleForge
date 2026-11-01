from _db import answer, make, reference

DATA = """
INSERT INTO customers VALUES (1, 'Zoe'), (2, 'Ana'), (3, 'Mo'), (4, 'Bea');
INSERT INTO orders VALUES
  (1, 1, '2026-05-01 00:00:00', 3000, 'paid'),
  (2, 2, '2026-05-31 23:59:59', 1000, 'paid'),
  (3, 2, '2026-05-15 10:00:00', 2000, 'paid'),
  (4, 3, '2026-05-02 00:00:00', 3000, 'refunded'),
  (5, 4, '2026-05-03 00:00:00', 2999, 'paid'),
  (6, 4, '2026-06-01 00:00:00', 500, 'paid'),
  (7, 1, '2026-06-30 12:00:00', 500, 'paid'),
  (8, 3, '2025-12-31 23:59:59', 99999, 'paid'),
  (9, 2, '2026-07-01 00:00:00', 99999, 'paid');
"""


def test_three_way_tie_and_boundaries():
    conn = make(DATA)
    got = answer(conn)
    assert got == reference(conn)
    assert [r for r in got if r[0] == "2026-05"] == [("2026-05", "Ana", 3000), ("2026-05", "Mo", 3000),
                                                     ("2026-05", "Zoe", 3000)]
    assert [r for r in got if r[0] == "2026-06"] == [("2026-06", "Bea", 500), ("2026-06", "Zoe", 500)]
    assert len(got) == 4 + 3 + 2
