import json
import sqlite3

from photosort.catalog import connect
from photosort.importer import import_dir


def photo(d, name, data, taken, offset=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    sc = {"taken": taken}
    if offset is not None:
        sc["offset"] = offset
    (d / (name + ".json")).write_text(json.dumps(sc))


def fresh(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    return lib, connect(str(tmp_path / "cat.db"), lib)


def rows(conn):
    return conn.execute("SELECT name, dest, taken_utc FROM photos ORDER BY id").fetchall()
