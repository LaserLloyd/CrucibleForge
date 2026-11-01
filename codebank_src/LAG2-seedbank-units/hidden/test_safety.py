import sqlite3

import pytest
from _seedfix import V2_ROWS, V3_EXPECT, cols, dump, make_db

from seedbank import db


def test_v1_chain(tmp_path):
    p = str(tmp_path / "old.db")
    rows = [(1, "Purple Sprouting Broccoli", "2 packets", ""), (2, "Mystery", "some", "?"),
            (3, "Runner Bean", "about 40 seeds", "")]
    make_db(p, version=1, rows=rows, sowings=[(1, 1, "2025-03-10", "A", 6)])
    db.connect(p).close()
    c = sqlite3.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    got = sorted(c.execute("SELECT id, family, amount_milli, unit, approx, notes FROM seeds"))
    assert got == [(1, "brassica", 2000, "packet", 0, ""), (2, "", None, None, 0, "?\n[quantity: some]"),
                   (3, "legume", 40000, "seed", 1, "")]
    assert c.execute("SELECT count(*) FROM sowings").fetchone()[0] == 1


def test_idempotent(tmp_path):
    p = str(tmp_path / "seeds.db")
    make_db(p)
    db.connect(p).close()
    first = dump(p)
    db.connect(p).close()
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(conn)
    assert not conn.in_transaction
    conn.close()
    assert dump(p) == first


def test_too_new_untouched(tmp_path):
    p = str(tmp_path / "seeds.db")
    make_db(p)
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 9")
    c.commit()
    c.close()
    before = dump(p)
    with pytest.raises(db.SchemaTooNew):
        db.connect(p)
    assert dump(p) == before


def test_failure_rolls_back_everything(tmp_path, monkeypatch):
    p = str(tmp_path / "seeds.db")
    make_db(p)
    before = dump(p)
    seen = {}

    def boom(conn):
        seen["cols"] = [r[1] for r in conn.execute("PRAGMA table_info(seeds)")]
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db, "MIGRATIONS", list(db.MIGRATIONS) + [(4, boom)])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 4)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(RuntimeError, match="disk on fire"):
        db.migrate(conn)
    assert "amount_milli" in seen["cols"] and "quantity" not in seen["cols"]
    assert not conn.in_transaction
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()
    assert dump(p) == before


def test_fresh_schema_matches_migrated(tmp_path):
    p1, p2 = str(tmp_path / "fresh.db"), str(tmp_path / "old.db")
    fresh = db.connect(p1)
    make_db(p2)
    old = db.connect(p2)
    assert cols(fresh) == cols(old)
    assert ("approx", "INTEGER", 1, "0", 0) in cols(fresh)
    assert fresh.execute("PRAGMA user_version").fetchone()[0] == 3
    for conn in (fresh, old):
        ins = "INSERT INTO seeds (name, created_at, amount_milli, unit, approx) VALUES ('x', 't', ?, ?, ?)"
        for bad in [(5, "bag", 0), (5, None, 0), (None, "seed", 0), (-1, "seed", 0), (5, "seed", 2)]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(ins, bad)
        conn.execute(ins, (5, "gram", 1))
        conn.execute(ins, (None, None, 0))
        conn.rollback()
