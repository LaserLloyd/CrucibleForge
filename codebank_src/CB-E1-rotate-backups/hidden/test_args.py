from _sh import make, run


def test_usage_errors(tmp_path):
    make(tmp_path, ["db-20261020-020000.tar.zst"])
    for args in ((), ("--keep", 0, tmp_path), ("--keep", "x", tmp_path), ("--keep", "-1", tmp_path),
                 ("--bogus", tmp_path), ("--keep", "2.5", tmp_path)):
        p = run(*args)
        assert p.returncode == 2, (args, p.returncode)
        assert p.stdout == "" and p.stderr.strip() != ""


def test_missing_dir(tmp_path):
    p = run(tmp_path / "nope")
    assert p.returncode == 3 and p.stdout == "" and p.stderr.strip() != ""
    f = tmp_path / "file"
    f.write_text("x")
    assert run(f).returncode == 3
