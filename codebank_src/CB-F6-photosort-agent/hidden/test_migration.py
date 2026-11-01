import sqlite3

from _ph import import_dir, photo
from photosort.catalog import connect

V1 = """
CREATE TABLE photos (id INTEGER PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL, dest TEXT NOT NULL,
                     taken_utc TEXT NOT NULL);
PRAGMA user_version = 1;
"""


def _release1_catalog(tmp_path):
    lib = tmp_path / "lib"
    (lib / "2024" / "05").mkdir(parents=True)
    (lib / "2024" / "05" / "IMG_0001.jpg").write_bytes(b"beach")
    (lib / "2024" / "05" / "IMG_0002.jpg").write_bytes(b"sunset")
    db = tmp_path / "cat.db"
    c = sqlite3.connect(db)
    c.executescript(V1)
    c.execute("INSERT INTO photos VALUES (1, 'IMG_0001.jpg', 5, '2024/05/IMG_0001.jpg', '2024-05-01T10:00:00Z')")
    c.execute("INSERT INTO photos VALUES (2, 'IMG_0002.jpg', 6, '2024/05/IMG_0002.jpg', '2024-05-01T19:00:00Z')")
    c.commit()
    c.close()
    return lib, db


def test_release1_catalog_keeps_working(tmp_path):
    lib, db = _release1_catalog(tmp_path)
    conn = connect(str(db), lib)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 2
    src = tmp_path / "phone"
    photo(src, "renamed_beach.jpg", b"beach", "2026:03:01 10:00:00")      # same bytes as a release-1 photo
    photo(src, "IMG_0002.jpg", b"new!!!", "2024:05:01 21:00:00")           # same name+size, other content
    r = import_dir(src, lib, conn)
    assert r == {"imported": ["2024/05/IMG_0002-1.jpg"], "skipped": ["renamed_beach.jpg"], "ignored": []}


def test_migrate_twice_is_harmless(tmp_path):
    lib, db = _release1_catalog(tmp_path)
    connect(str(db), lib).close()
    conn = connect(str(db), lib)
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 2
