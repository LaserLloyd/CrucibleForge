from _sh import left, make, run


def test_stamp_not_mtime(tmp_path):
    names = [f"db-202610{d}-020000.tar.zst" for d in ("20", "21", "22", "23", "24")]
    # restored from the external drive: oldest backup got the newest mtime
    make(tmp_path, names, mtime_order=list(reversed(names)))
    p = run(tmp_path)
    assert p.stdout == "deleted db-20261020-020000.tar.zst\ndeleted db-20261021-020000.tar.zst\n"
    assert left(tmp_path) == names[2:]


def test_time_of_day_and_bytewise_output(tmp_path):
    names = ["B-20261024-230000.tar.zst", "B-20261024-010000.tar.zst", "B-20261023-235959.tar.zst",
             "a-20261024-120000.tar.zst", "a-20261024-120001.tar.zst", "a-20261024-115959.tar.zst"]
    make(tmp_path, names, mtime_order=names)
    p = run("--keep", 1, tmp_path)
    assert p.stdout.splitlines() == ["deleted B-20261023-235959.tar.zst", "deleted B-20261024-010000.tar.zst",
                                     "deleted a-20261024-115959.tar.zst", "deleted a-20261024-120000.tar.zst"]
