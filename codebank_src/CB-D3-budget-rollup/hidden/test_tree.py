from _db import answer, make, reference

DATA = """
INSERT INTO categories VALUES (10, NULL, 'A'), (11, 10, 'B'), (12, 11, 'C'), (13, 12, 'D'),
                              (14, 10, 'A2'), (15, NULL, 'A B');
INSERT INTO spend VALUES (1, 13, '2026-05-31', 400), (2, 12, '2026-05-01', 100), (3, 14, '2026-06-01', 999),
                         (4, 11, '2026-05-10', -50), (5, 15, '2026-05-02', 7);
INSERT INTO budgets VALUES (12, '2026-05', 450), (10, '2026-05', 300);
"""


def test_deep_tree_totals_and_order():
    conn = make(DATA)
    got = answer(conn, "2026-05")
    assert got == [("A", 0, 450, 300, -150, 1), ("A > A2", 0, 0, None, None, 0),
                   ("A > B", -50, 450, None, None, 0), ("A > B > C", 100, 500, 450, -50, 1),
                   ("A > B > C > D", 400, 400, None, None, 0), ("A B", 7, 7, None, None, 0)]
    assert got == reference(conn, "2026-05")
