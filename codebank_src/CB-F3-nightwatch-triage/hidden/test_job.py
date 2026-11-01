import io
from datetime import datetime, timezone

from _tz import backup, local_zone
from nightwatch.main import run


def test_nightly_run_in_tokyo(tmp_path):
    now_ts = 1_792_000_000.0
    now = datetime.fromtimestamp(now_ts, timezone.utc)
    photos, home = tmp_path / "photos", tmp_path / "home"
    photos.mkdir()
    home.mkdir()
    backup(photos, "p1.tar.zst", 19, now_ts)
    backup(photos, "p0.tar.zst", 43, now_ts)
    backup(home, "home-1.tar.zst", 7.4, now_ts)
    cfg = tmp_path / "nw.toml"
    cfg.write_text(f'[verify]\nmax_age_h = 26\n[[target]]\nname = "photos"\ndir = "{photos}"\npattern = "*.tar.zst"\n'
                   f'[[target]]\nname = "home"\ndir = "{home}"\npattern = "home-*.tar.zst"\n')
    out, err = io.StringIO(), io.StringIO()
    with local_zone("Asia/Tokyo"):
        rc = run(str(cfg), now=now, out=out, err=err)
    assert rc == 0, (out.getvalue(), err.getvalue())
    assert out.getvalue() == "nightwatch: photos=FRESH(19.0h) home=FRESH(7.4h)\n"
