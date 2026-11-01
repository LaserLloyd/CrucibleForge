from _ph import fresh, import_dir, photo, rows


def test_local_month_and_utc_instant(tmp_path):
    src = tmp_path / "phone"
    photo(src, "a.jpg", b"A", "2026:03:31 23:30:00", "+09:00")
    photo(src, "b.jpg", b"B", "2026:04:01 00:10:00", "+02:00")
    photo(src, "c.jpg", b"C", "2026:04:30 20:00:00", "-05:30")
    photo(src, "d.jpg", b"D", "2026:12:31 23:59:59")
    lib, conn = fresh(tmp_path)
    r = import_dir(src, lib, conn)
    assert r["imported"] == ["2026/03/a.jpg", "2026/04/b.jpg", "2026/04/c.jpg", "2026/12/d.jpg"]
    assert [t for _, _, t in rows(conn)] == ["2026-03-31T14:30:00Z", "2026-03-31T22:10:00Z",
                                            "2026-05-01T01:30:00Z", "2026-12-31T23:59:59Z"]
