import io
import time

from _tz import backup
from nightwatch.main import run


def test_missing_and_stale_and_bad_dir(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    backup(a, "x.tar.zst", 40, time.time())
    cfg = tmp_path / "nw.toml"
    cfg.write_text(f'[verify]\nmax_age_hours = 26\n[[target]]\nname = "a"\ndir = "{a}"\npattern = "*.tar.zst"\n'
                   f'[[target]]\nname = "b"\ndir = "{b}"\npattern = "*.tar.zst"\n')
    out = io.StringIO()
    assert run(str(cfg), out=out, err=io.StringIO()) == 1
    assert out.getvalue() == "nightwatch: a=STALE(40.0h) b=MISSING(-)\n"
    cfg.write_text('[verify]\nmax_age_hours = 26\n[[target]]\nname = "c"\ndir = "/nonexistent/x"\npattern = "*"\n')
    err = io.StringIO()
    assert run(str(cfg), out=io.StringIO(), err=err) == 2 and "nightwatch:" in err.getvalue()
