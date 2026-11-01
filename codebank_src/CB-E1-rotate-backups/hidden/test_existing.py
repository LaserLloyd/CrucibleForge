from _sh import left, make, run


def test_default_and_keep(tmp_path):
    names = [f"db-2026102{d}-020000.tar.zst" for d in range(5)]
    make(tmp_path, names, mtime_order=names)
    p = run("--keep", 4, tmp_path)
    assert (p.returncode, p.stdout) == (0, f"deleted {names[0]}\n")
    p = run(tmp_path)
    assert (p.returncode, p.stdout) == (0, f"deleted {names[1]}\n")
    assert run(tmp_path).stdout == ""
    assert left(tmp_path) == names[2:]


def test_empty_dir(tmp_path):
    p = run(tmp_path)
    assert p.returncode == 0 and p.stdout == ""
