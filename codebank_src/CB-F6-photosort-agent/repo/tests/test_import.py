import json

from photosort.catalog import connect
from photosort.importer import import_dir


def _photo(d, name, data, taken, offset="+00:00"):
    (d / name).write_bytes(data)
    (d / (name + ".json")).write_text(json.dumps({"taken": taken, "offset": offset}))


def test_import_and_folders(tmp_path):
    src, lib = tmp_path / "phone", tmp_path / "lib"
    src.mkdir(); lib.mkdir()
    _photo(src, "IMG_1.jpg", b"one", "2026:05:02 10:00:00")
    (src / "notes.txt").write_text("x")
    conn = connect(str(tmp_path / "cat.db"), lib)
    r = import_dir(src, lib, conn)
    assert r == {"imported": ["2026/05/IMG_1.jpg"], "skipped": [], "ignored": ["notes.txt"]}
