import sqlite3

from _seedfix import SOWINGS, TS, V2_ROWS, V3_EXPECT, make_db

from seedbank import db


def _migrated(tmp_path):
    p = str(tmp_path / "seeds.db")
    make_db(p)
    conn = db.connect(p)
    conn.close()
    return p


def test_rows_converted(tmp_path):
    p = _migrated(tmp_path)
    c = sqlite3.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    got = {r[0]: r[1:] for r in c.execute("SELECT id, amount_milli, unit, approx, notes FROM seeds")}
    assert got == V3_EXPECT


def test_other_columns_untouched(tmp_path):
    p = _migrated(tmp_path)
    c = sqlite3.connect(p)
    names = [r[1] for r in c.execute("PRAGMA table_info(seeds)")]
    assert "quantity" not in names
    assert {"amount_milli", "unit", "approx"} <= set(names)
    rows = {r[0]: r[1:] for r in c.execute(
        "SELECT id, name, variety, family, harvested, source, created_at FROM seeds")}
    assert rows == {sid: (name, "v%d" % sid, "fam%d" % sid, 2020 + sid % 5, "src%d" % sid, TS)
                    for sid, name, _q, _n in V2_ROWS}
    idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert {"idx_seeds_name", "idx_sowings_seed"} <= idx


def test_sowings_survive_and_cascade(tmp_path):
    p = _migrated(tmp_path)
    c = sqlite3.connect(p)
    assert sorted(c.execute("SELECT * FROM sowings").fetchall()) == sorted(SOWINGS)
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()
    conn = db.connect(p)
    conn.execute("DELETE FROM seeds WHERE id = 1")
    conn.commit()
    left = sorted(r[0] for r in conn.execute("SELECT id FROM sowings"))
    assert left == [3, 4, 5]
    try:
        conn.execute("INSERT INTO sowings (seed_id, sown_on) VALUES (999, '2025-06-01')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("foreign key no longer enforced after the migration")


def test_api_reads_migrated_rows(tmp_path):
    from _seedfix import call
    from seedbank.api import make_app
    p = _migrated(tmp_path)
    app = make_app(db.connect(p))
    assert call(app, "GET", "/seeds/2")[1]["amount"] == {"value": "30", "unit": "seed", "approx": True}
    assert call(app, "GET", "/seeds/3")[1]["quantity"] == "42.524 g"
    b = call(app, "GET", "/seeds/7")[1]
    assert b["amount"] is None and b["quantity"] == "" and b["notes"] == "[quantity: 2 Packets from Gran]"
