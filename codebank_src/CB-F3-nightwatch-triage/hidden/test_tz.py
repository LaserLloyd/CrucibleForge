import time
from datetime import datetime, timezone

from _tz import backup, local_zone
from nightwatch.check import age_hours, check_target


def test_age_independent_of_local_zone(tmp_path):
    now_ts = 1_792_000_000.0
    now = datetime.fromtimestamp(now_ts, timezone.utc)
    p = backup(tmp_path, "a.tar.zst", 19, now_ts)
    for zone in ("Asia/Tokyo", "Europe/Berlin", "America/New_York", "UTC"):
        with local_zone(zone):
            assert round(age_hours(p.stat().st_mtime, now), 3) == 19.0, zone
            r = check_target({"name": "photos", "dir": str(tmp_path), "pattern": "*.tar.zst"}, 26, now)
            assert r == {"name": "photos", "status": "FRESH", "age_hours": 19.0}, zone


def test_default_now_is_current_time(tmp_path):
    with local_zone("Asia/Tokyo"):
        p = backup(tmp_path, "b.tar.zst", 3, time.time())
        assert 2.9 < age_hours(p.stat().st_mtime) < 3.1
