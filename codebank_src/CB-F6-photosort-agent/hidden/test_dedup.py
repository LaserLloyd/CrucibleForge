from _ph import fresh, import_dir, photo, rows


def test_same_name_different_content_both_imported(tmp_path):
    lib, conn = fresh(tmp_path)
    photo(tmp_path / "old", "IMG_0412.jpg", b"x" * 100, "2026:03:01 10:00:00")
    assert import_dir(tmp_path / "old", lib, conn)["imported"] == ["2026/03/IMG_0412.jpg"]
    photo(tmp_path / "new", "IMG_0412.jpg", b"y" * 100, "2026:03:01 11:00:00")
    r = import_dir(tmp_path / "new", lib, conn)
    assert r == {"imported": ["2026/03/IMG_0412-1.jpg"], "skipped": [], "ignored": []}


def test_same_content_different_name_skipped_even_within_one_import(tmp_path):
    lib, conn = fresh(tmp_path)
    src = tmp_path / "p"
    photo(src, "a.JPG", b"same", "2026:03:01 10:00:00")
    photo(src, "b.jpeg", b"same", "2026:03:05 10:00:00")
    photo(src, "c.heic", b"other", "2026:03:05 10:00:00")
    r = import_dir(src, lib, conn)
    assert r == {"imported": ["2026/03/a.JPG", "2026/03/c.heic"], "skipped": ["b.jpeg"], "ignored": []}
    r = import_dir(src, lib, conn)
    assert r["imported"] == [] and r["skipped"] == ["a.JPG", "b.jpeg", "c.heic"]
