import random

from _db import answer, make, reference


def test_random_households():
    rnd = random.Random(77)
    for trial in range(40):
        n = rnd.randint(2, 12)
        cats = []
        for i in range(1, n + 1):
            parent = rnd.choice([None] + [c[0] for c in cats]) if cats else None
            cats.append((i, parent, rnd.choice(["Food", "Fun", "Car", "Home", "Kids", "Pets"]) + str(i)))
        lines = ["INSERT INTO categories VALUES " + ", ".join(f"({i}, {'NULL' if p is None else p}, '{nm}')"
                                                          for i, p, nm in cats) + ";"]
        bud = {(rnd.randint(1, n), rnd.choice(["2026-02", "2026-03"])) for _ in range(rnd.randint(0, 8))}
        if bud:
            lines.append("INSERT INTO budgets VALUES " + ", ".join(f"({c}, '{m}', {rnd.randint(1, 50) * 500})"
                                                               for c, m in sorted(bud)) + ";")
        sp = [(k, rnd.randint(1, n), f"2026-0{rnd.randint(2, 3)}-{rnd.randint(1, 28):02d}",
               rnd.choice([-1, 1, 1, 1]) * rnd.randint(1, 40) * 250) for k in range(1, rnd.randint(1, 40))]
        if sp:
            lines.append("INSERT INTO spend VALUES " + ", ".join(f"({k}, {c}, '{d}', {v})" for k, c, d, v in sp) + ";")
        conn = make("\n".join(lines))
        for month in ("2026-02", "2026-03", "2026-04"):
            assert answer(conn, month) == reference(conn, month), (trial, month)
