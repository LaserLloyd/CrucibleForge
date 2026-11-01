from _ph import fresh, import_dir, photo


def test_ignored_and_order(tmp_path):
    lib, conn = fresh(tmp_path)
    src = tmp_path / "p"
    photo(src, "b.jpg", b"1", "2026:01:02 03:04:05", "+00:00")
    photo(src, "a.png", b"2", "2026:01:02 03:04:05", "+00:00")
    (src / "c.jpg").write_bytes(b"no sidecar")
    r = import_dir(src, lib, conn)
    assert r == {"imported": ["2026/01/b.jpg"], "skipped": [], "ignored": ["a.png", "c.jpg"]}
    assert (lib / "2026" / "01" / "b.jpg").read_bytes() == b"1"
