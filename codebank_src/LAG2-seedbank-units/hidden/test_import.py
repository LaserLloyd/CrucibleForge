import contextlib
import io
import sqlite3

import pytest
from _seedfix import dump

from seedbank import cli, db, export

V3 = "id,name,variety,family,amount,unit,approx,harvested,source,notes\n"
V2 = "id,name,variety,family,quantity,harvested,source,notes\n"


def _db(tmp_path):
    p = str(tmp_path / "s.db")
    conn = db.connect(p)
    ins = ("INSERT INTO seeds (id, name, variety, family, amount_milli, unit, approx, harvested, source, notes,"
           " created_at) VALUES (?, ?, '', ?, ?, ?, ?, ?, '', ?, 't')")
    conn.execute(ins, (1, "Kale", "brassica", 2000, "packet", 0, 2024, "keep"))
    conn.execute(ins, (2, "Bean", "legume", 40000, "seed", 1, None, ""))
    conn.commit()
    return p, conn


def _rows(conn):
    return sorted(tuple(r) for r in conn.execute(
        "SELECT id, name, variety, family, amount_milli, unit, approx, harvested, source, notes FROM seeds"))


def test_round_trip_and_v3_rows(tmp_path):
    p, conn = _db(tmp_path)
    fh = io.StringIO()
    export.export_csv(conn, fh)
    before = _rows(conn)
    assert export.import_csv(conn, io.StringIO(fh.getvalue())) == 2
    assert _rows(conn) == before
    text = V3 + ('1,Kale,Red,,0.25,packet,,2023,Gran,"a\nb"\n'
                 ',Dill,,,1.0005,gram,1,,,\n'
                 ',Mystery Mix,,,,,,,,\n')
    assert export.import_csv(conn, io.StringIO(text)) == 3
    rows = _rows(conn)
    assert rows[0] == (1, "Kale", "Red", "brassica", 250, "packet", 0, 2023, "Gran", "a\nb")
    assert rows[1] == (2, "Bean", "", "legume", 40000, "seed", 1, None, "", "")
    assert rows[2][1:] == ("Dill", "", "apiaceae", 1001, "gram", 1, None, "", "")
    assert rows[3][1:] == ("Mystery Mix", "", "", None, None, 0, None, "", "")


def test_v2_header(tmp_path):
    p, conn = _db(tmp_path)
    text = V2 + "2,Bean,Blue,,about 2 packets,2022,,\n,Leek,,,half an ounce,,,\n,Chard,,amaranth,,,,\n"
    assert export.import_csv(conn, io.StringIO(text)) == 3
    rows = _rows(conn)
    assert rows[1] == (2, "Bean", "Blue", "legume", 2000, "packet", 1, 2022, "", "")
    assert [r[1:7] for r in rows[2:]] == [("Leek", "", "allium", 14175, "gram", 0),
                                          ("Chard", "", "amaranth", None, None, 0)]


@pytest.mark.parametrize("text,line", [
    (V3 + "1,Kale,,,1,packet,0,,,\n,Pea,,,1,bag,0,,,\n", 3),
    (V3 + ",Pea,,,1.5,seed,0,,,\n", 2),
    (V3 + ",Pea,,,1,seed,2,,,\n", 2),
    (V3 + ",Pea,,,1/2,packet,0,,,\n", 2),
    (V3 + ",Pea,,,,packet,,,,\n", 2),
    (V3 + ",Pea,,,1,seed,0,,,\n9,Ghost,,,,,,,,\n", 3),
    (V3 + "1,Kale,,,,,,,,\n,Pea,,,,,,,,\n1,Kale again,,,,,,,,\n", 4),
    (V3 + ',Pea,,,,,,,,"multi\nline"\n,,,,,,,,,\n', 3),
    (V3 + ",Pea,,,,,,soon,,\n", 2),
    (V2 + "1,Kale,,,2 packets from Gran,,,\n", 2),
    (V2 + ",Pea,,,,,,\n,Leek,,,~ ~3,,,\n", 3),
])
def test_errors_change_nothing(tmp_path, text, line):
    p, conn = _db(tmp_path)
    conn.close()
    before = dump(p)
    conn = db.connect(p)
    with pytest.raises(export.CSVImportError) as ei:
        export.import_csv(conn, io.StringIO(text))
    assert str(ei.value).startswith("line %d: " % line), str(ei.value)
    assert isinstance(ei.value, ValueError)
    assert not conn.in_transaction
    conn.close()
    assert dump(p) == before


def test_bad_header(tmp_path):
    p, conn = _db(tmp_path)
    with pytest.raises(export.CSVImportError):
        export.import_csv(conn, io.StringIO("id,name,quantity\n,Pea,1\n"))


def test_cli_import(tmp_path):
    p, conn = _db(tmp_path)
    conn.close()
    good, bad = tmp_path / "good.csv", tmp_path / "bad.csv"
    good.write_text(V3 + ",Pea,,,3,packet,0,,,\n,Leek,,,,,,,,\n", encoding="utf-8")
    bad.write_text(V3 + ",Onion,,,,,,,,\n,Pea,,,3,tin,0,,,\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["--db", p, "import", str(bad)])
        except SystemExit as e:
            code = e.code
    assert code == 1 and err.getvalue().startswith("error: line 3: ")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["--db", p, "import", str(good)]) == 0
    assert out.getvalue().strip() == "imported 2 seeds"
    names = [r[0] for r in sqlite3.connect(p).execute("SELECT name FROM seeds ORDER BY id")]
    assert names == ["Kale", "Bean", "Pea", "Leek"]
