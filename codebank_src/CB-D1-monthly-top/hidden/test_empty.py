from _db import answer, make, reference

DATA = """
INSERT INTO customers VALUES (1, 'Ana'), (2, 'Ben');
INSERT INTO orders VALUES
  (1, 1, '2026-01-10 00:00:00', 4000, 'refunded'),
  (2, 2, '2026-02-10 00:00:00', 9000, 'pending'),
  (3, 2, '2026-04-10 00:00:00', 0, 'paid');
INSERT INTO refunds VALUES (1, 1, 4000, '2026-03-01 00:00:00');
"""


def test_quiet_negative_and_zero_months():
    conn = make(DATA)
    got = answer(conn)
    assert got == [("2026-01", "Ana", 4000), ("2026-02", None, 0), ("2026-03", None, 0),
                   ("2026-04", None, 0), ("2026-05", None, 0), ("2026-06", None, 0)]
    assert got == reference(conn)


def test_empty_database():
    conn = make("")
    assert answer(conn) == [(f"2026-0{m}", None, 0) for m in range(1, 7)]
