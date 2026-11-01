from _sh import left, make, run


def test_dry_run_deletes_nothing(tmp_path):
    names = [f"db-2026102{d}-020000.tar.zst" for d in range(5)]
    make(tmp_path, names)
    for args in (("--dry-run", "--keep", 2, tmp_path), ("--keep", 2, "--dry-run", tmp_path), (tmp_path, "--dry-run", "--keep", 2)):
        p = run(*args)
        assert p.returncode == 0
        assert p.stdout.splitlines() == [f"would delete {n}" for n in names[:3]], args
        assert left(tmp_path) == names
