import os
import time
from contextlib import contextmanager


@contextmanager
def local_zone(name):
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def backup(dir_, name, age_hours, now_ts):
    p = dir_ / name
    p.write_text("x")
    t = now_ts - age_hours * 3600
    os.utime(p, (t, t))
    return p
