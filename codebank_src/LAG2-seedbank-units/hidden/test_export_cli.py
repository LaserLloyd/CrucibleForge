import contextlib
import csv
import io
import sqlite3

from seedbank import cli, db, export


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_export_columns():
    conn = db.connect()
    ins = ("INSERT INTO seeds (name, family, amount_milli, unit, approx, harvested, notes, created_at)"
           " VALUES (?, ?, ?, ?, ?, ?, ?, 't')")
    conn.execute(ins, ("Kale", "brassica", 2000, "packet", 0, 2024, "line1\nline2"))
    conn.execute(ins, ("Bean", "legume", 40000, "seed", 1, None, ""))
    conn.execute(ins, ("Chard", "amaranth", None, None, 0, None, ""))
    conn.execute(ins, ("Dill", "apiaceae", 14175, "gram", 0, None, ""))
    conn.commit()
    fh = io.StringIO()
    assert export.export_csv(conn, fh) == 4
    rows = list(csv.reader(io.StringIO(fh.getvalue())))
    assert rows[0] == ["id", "name", "variety", "family", "amount", "unit", "approx", "harvested", "source", "notes"]
    body = {r[1]: r[4:8] + [r[9]] for r in rows[1:]}
    assert body == {"Kale": ["2", "packet", "0", "2024", "line1\nline2"], "Bean": ["40", "seed", "1", "", ""],
                    "Chard": ["", "", "", "", ""], "Dill": ["14.175", "gram", "0", "", ""]}


def test_cli_add_and_list(tmp_path):
    p = str(tmp_path / "s.db")
    assert run(["--db", p, "add", "Parsnip", "--qty", "half a packet"])[0] == 0
    assert run(["--db", p, "add", "Kale", "--amount", "2", "--unit", "packet", "--approx"])[0] == 0
    assert run(["--db", p, "add", "Leek", "--amount", "0.25", "--unit", "gram"])[0] == 0
    assert run(["--db", p, "add", "Chard"])[0] == 0
    code, out, _ = run(["--db", p, "list"])
    lines = {l.split()[1]: l.rstrip().rsplit("  ", 1)[1] for l in out.splitlines()}
    assert lines == {"Parsnip": "0.5 packets", "Kale": "about 2 packets", "Leek": "0.25 g", "Chard": "-"}


def test_cli_rejects(tmp_path):
    p = str(tmp_path / "s.db")
    run(["--db", p, "add", "Chard"])
    for argv in (["--qty", "2 packets", "--amount", "2", "--unit", "packet"], ["--amount", "2"],
                 ["--unit", "packet"], ["--qty", "1", "--unit", "seed"]):
        assert run(["--db", p, "add", "X"] + argv)[0] == 2, argv
    for argv in (["--qty", "lots"], ["--amount", "1.5", "--unit", "seed"], ["--amount", "2", "--unit", "bag"],
                 ["--amount", "-2", "--unit", "gram"]):
        code, _, err = run(["--db", p, "add", "X"] + argv)
        assert code == 1 and err.startswith("error: "), argv
    c = sqlite3.connect(p)
    assert c.execute("SELECT name FROM seeds").fetchall() == [("Chard",)]
