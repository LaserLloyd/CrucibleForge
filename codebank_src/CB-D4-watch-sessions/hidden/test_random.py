import random
from datetime import datetime, timedelta

from _db import answer, make, reference


def test_random_logs():
    rnd = random.Random(404)
    for trial in range(40):
        users = [(i, f"u{i:02d}", rnd.choice([-480, -300, 0, 60, 330, 540])) for i in range(1, rnd.randint(2, 5))]
        lines = ["INSERT INTO users VALUES " + ", ".join(f"({i}, '{n}', {o})" for i, n, o in users) + ";"]
        plays = []
        for pid in range(1, rnd.randint(2, 60)):
            uid = rnd.choice(users)[0]
            t = datetime(2026, 2, 1) + timedelta(minutes=rnd.randint(0, 3 * 24 * 60), seconds=rnd.choice([0, 0, 1, 30]))
            plays.append(f"({pid}, {uid}, '{t:%Y-%m-%d %H:%M:%S}', {rnd.choice([60, 300, 900, 1799, 1800, 3600, 7200])})")
        rnd.shuffle(plays)
        lines.append("INSERT INTO plays VALUES " + ", ".join(plays) + ";")
        conn = make("\n".join(lines))
        assert answer(conn) == reference(conn), trial
