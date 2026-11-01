import sqlite3

from _v1 import make_v1
from pulsecount.db import connect
from pulsecount.migrate import MIGRATIONS, migrate, version


def test_versions_and_noop(tmp_path):
    path = str(tmp_path / "old.db")
    make_v1(path, [(1, "d", "n", 1, "t", "t", None), (2, "d", "n", 2, "t", "t", None)])
    conn = connect(path)
    v = version(conn)
    assert v == len(MIGRATIONS) and v >= 2
    before = conn.execute("SELECT * FROM counters").fetchall()
    assert migrate(conn) == v and migrate(conn) == v
    assert [tuple(r) for r in conn.execute("SELECT * FROM counters").fetchall()] == [tuple(r) for r in before]


def test_fresh_database_is_latest_and_unique(tmp_path):
    conn = connect(str(tmp_path / "new.db"))
    assert version(conn) == len(MIGRATIONS) and version(conn) >= 2
    conn.execute("INSERT INTO counters (day, name, count, first_seen, last_seen) VALUES ('d','n',1,'t','t')")
    try:
        conn.execute("INSERT INTO counters (day, name, count, first_seen, last_seen) VALUES ('d','n',1,'t','t')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate accepted on a fresh database")
