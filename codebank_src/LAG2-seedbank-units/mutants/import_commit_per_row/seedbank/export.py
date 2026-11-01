"""CSV export for Gran's spreadsheet."""
import csv

from . import quantity as _q
from . import store, util, viability
from .quantity import format_number

HEADER = ["id", "name", "variety", "family", "amount", "unit", "approx", "harvested", "source", "notes"]
HEADER_V2 = ["id", "name", "variety", "family", "quantity", "harvested", "source", "notes"]


class CSVImportError(ValueError):
    pass


def rows(conn):
    for s in store.list_seeds(conn):
        a = s.amount
        amt = ["", "", ""] if a is None else [format_number(a.milli), a.unit, "1" if a.approx else "0"]
        yield [s.id, s.name, s.variety, s.family, *amt,
               "" if s.harvested is None else s.harvested, s.source, s.notes]


def export_csv(conn, fh):
    """Write every seed to the text file ``fh`` (sorted by name); returns the row count."""
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(HEADER)
    n = 0
    for r in rows(conn):
        w.writerow(r)
        n += 1
    return n


def _amount(rec, v2):
    if v2:
        a = _q.parse_quantity(rec["quantity"])
    elif rec["amount"] == "" and rec["unit"] == "" and rec["approx"] == "":
        a = None
    else:
        if rec["approx"] not in ("", "0", "1"):
            raise ValueError("approx must be 0 or 1")
        a = _q.make_amount(rec["amount"], rec["unit"], rec["approx"] == "1")
    return (None, None, 0) if a is None else (a.milli, a.unit, int(a.approx))


def import_csv(conn, fh):
    """Read an export (v3 or v2 header) back in, all rows or none."""
    reader = csv.reader(fh)
    header = next(reader, None)
    if header == HEADER:
        v2 = False
    elif header == HEADER_V2:
        v2 = True
    else:
        raise CSVImportError("line 1: unknown header")
    n, seen = 0, set()
    try:
        for recno, row in enumerate(reader, start=2):
            try:
                if len(row) != len(header):
                    raise ValueError("wrong number of columns")
                rec = dict(zip(header, row))
                name = rec["name"].strip()
                if not name:
                    raise ValueError("name is required")
                family = rec["family"].strip() or viability.family_of(name)
                harvested = util.year_or_none(rec["harvested"].strip())
                milli, unit, approx = _amount(rec, v2)
                vals = (name, rec["variety"].strip(), family, milli, unit, approx, harvested,
                        rec["source"].strip(), rec["notes"])
                sid = rec["id"].strip()
                if sid:
                    if not sid.isdigit() or int(sid) in seen:
                        raise ValueError(f"id {sid} twice or not a number")
                    seen.add(int(sid))
                    cur = conn.execute("UPDATE seeds SET name = ?, variety = ?, family = ?, amount_milli = ?, "
                                       "unit = ?, approx = ?, harvested = ?, source = ?, notes = ? WHERE id = ?",
                                       (*vals, int(sid)))
                    if not cur.rowcount:
                        raise ValueError(f"no seed with id {sid}")
                else:
                    conn.execute("INSERT INTO seeds (name, variety, family, amount_milli, unit, approx, harvested, "
                                 "source, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (*vals, util.now_iso()))
                n += 1
                conn.commit()
            except CSVImportError:
                raise
            except ValueError as e:
                raise CSVImportError(f"line {recno}: {e}") from None
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return n
