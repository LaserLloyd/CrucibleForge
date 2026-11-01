from _db import answer, make, reference

DATA = """
INSERT INTO categories VALUES (1, NULL, 'Fun'), (2, 1, 'Games'), (3, NULL, 'Car');
INSERT INTO budgets VALUES (1, '2025-12', 10000), (1, '2026-01', 10000), (2, '2025-12', 3000),
                           (2, '2026-01', 3000), (3, '2025-12', 5000), (3, '2026-01', 5000);
INSERT INTO spend VALUES (1, 2, '2025-12-31', 2500), (2, 1, '2025-12-01', 1000), (3, 3, '2025-12-15', 7000),
                         (4, 2, '2026-01-01', 100), (5, 3, '2025-11-30', -9000);
"""


def test_carry_over_across_year_and_overspent_carries_nothing():
    conn = make(DATA)
    got = answer(conn, "2026-01")
    assert got == [("Car", 0, 0, 5000, 5000, 0),
                   ("Fun", 0, 100, 10000 + 6500, 16400, 0),
                   ("Fun > Games", 100, 100, 3000 + 500, 3400, 0)]
    assert got == reference(conn, "2026-01")


def test_budget_only_previous_month_gives_null():
    conn = make(DATA)
    assert answer(conn, "2026-02")[0] == ("Car", 0, 0, None, None, 0)
