import random
from datetime import timedelta

from _nb import UTC, brief, utc
from _refnb.dispatch import tick as ref_tick
from nudgebot.dispatch import tick
from nudgebot.state import load_state, save_state


def test_random_households(tmp_path):
    rnd = random.Random(2026)
    for trial in range(12):
        chores = [{"id": f"c{i}", "title": f"t{i}", "every": rnd.choice(["daily", "weekly", "2d", "3d"]),
                   "at": rnd.choice(["01:30", "02:30", "06:00", "07:00", "19:00", "22:15", "23:59"]),
                   "start": f"2026-{rnd.choice(['02', '03', '10'])}-{rnd.randint(1, 28):02d}",
                   "rotate": rnd.random() < 0.6} for i in range(rnd.randint(1, 4))]
        members = ["Ana", "Ben", "Cleo", "Dan", "Eve"][: rnd.randint(2, 5)]
        quiet = rnd.choice(["22:00-07:00", "00:00-00:00", "13:00-14:00", "23:00-02:30"])
        cfg = {"tz": "America/Chicago", "quiet": quiet, "members": members, "chores": chores}
        t = utc(2026, rnd.choice([2, 3, 10]), 1, 0)
        st, rst = {}, {}
        p = tmp_path / f"s{trial}.json"
        for step in range(900):
            t += timedelta(minutes=rnd.choice([5, 5, 5, 60, 600, 3000]))
            if rnd.random() < 0.02 and len(cfg["members"]) > 1:
                gone = rnd.choice(cfg["members"])
                cfg = {**cfg, "members": [m for m in cfg["members"] if m != gone]}
            got = brief(tick(t, cfg, st))
            want = brief(ref_tick(t, cfg, rst))
            assert got == want, (trial, step, t)
            if rnd.random() < 0.05:
                save_state(p, st)
                st = load_state(p)
