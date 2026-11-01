import random

from _db import answer, make, reference


def test_refunds_count_in_refund_month():
    data = """
    INSERT INTO customers VALUES (1, 'Ana'), (2, 'Ben'), (3, 'Cy');
    INSERT INTO orders VALUES
      (1, 1, '2026-03-05 00:00:00', 6000, 'refunded'),
      (2, 2, '2026-03-06 00:00:00', 5000, 'paid'),
      (3, 1, '2026-06-01 00:00:00', 1000, 'paid'),
      (4, 3, '2026-06-02 00:00:00', 800, 'paid'),
      (5, 2, '2026-01-02 00:00:00', 300, 'paid');
    INSERT INTO refunds VALUES (1, 1, 6000, '2026-06-20 00:00:00'), (2, 2, 100, '2026-03-30 00:00:00');
    """
    conn = make(data)
    got = answer(conn)
    assert ("2026-03", "Ana", 6000) in got and ("2026-06", "Cy", 800) in got
    assert got == reference(conn)


def test_random_shops_match_reference():
    rnd = random.Random(42)
    for trial in range(25):
        lines = ["INSERT INTO customers VALUES " + ", ".join(f"({i}, 'c{i:02d}')" for i in range(1, 7)) + ";"]
        orders = []
        for oid in range(1, rnd.randint(5, 40)):
            m = rnd.randint(1, 7)
            d = rnd.randint(1, 28)
            st = rnd.choice(["paid", "paid", "refunded", "pending"])
            orders.append((oid, rnd.randint(1, 6), f"2026-{m:02d}-{d:02d} 12:00:00", rnd.choice([500, 1000, 1500, 2000]), st))
        if orders:
            lines.append("INSERT INTO orders VALUES " + ", ".join(f"({o[0]}, {o[1]}, '{o[2]}', {o[3]}, '{o[4]}')" for o in orders) + ";")
            refs = [(i, o[0], o[3] // rnd.choice([1, 2]), f"2026-{rnd.randint(1, 6):02d}-15 08:00:00")
                    for i, o in enumerate([o for o in orders if o[4] == "refunded"], 1)]
            if refs:
                lines.append("INSERT INTO refunds VALUES " + ", ".join(f"({r[0]}, {r[1]}, {r[2]}, '{r[3]}')" for r in refs) + ";")
        conn = make("\n".join(lines))
        assert answer(conn) == reference(conn), trial
