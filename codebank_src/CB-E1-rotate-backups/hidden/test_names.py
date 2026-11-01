from _sh import left, make, run

KEEP = ["home server-db-20261023-020000.tar.zst", "home server-db-20261024-020000.tar.zst"]
OLD = ["home server-db-20261020-020000.tar.zst"]
OTHER = ["home server-20261019-010000.tar.zst", "home server-20261020-010000.tar.zst"]
ODD = ["notes.txt", "db-20261021-020000.tar.zst.part", "db-2026-1021.tar.zst", "db-20261021-0200.tar.zst",
       "-20261021-020000.tar.zst"]


def test_prefixes_with_spaces_and_dashes(tmp_path):
    make(tmp_path, KEEP + OLD + OTHER + ODD)
    p = run("--keep", 2, tmp_path)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "deleted home server-db-20261020-020000.tar.zst\n"
    assert left(tmp_path) == sorted(KEEP + OTHER + ODD)


def test_subfolders_and_dotted_prefix(tmp_path):
    sub = tmp_path / "old"
    sub.mkdir()
    make(sub, ["x-20200101-000000.tar.zst"])
    names = [f"v1.2-app-2026102{i}-000000.tar.zst" for i in range(5)]
    make(tmp_path, names)
    p = run("--keep", 1, tmp_path)
    assert p.returncode == 0
    assert p.stdout.splitlines() == [f"deleted {n}" for n in names[:4]]
    assert left(sub) == ["x-20200101-000000.tar.zst"]
