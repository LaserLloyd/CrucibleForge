"""CSV export for Gran's spreadsheet."""
import csv

from . import store

HEADER = ["id", "name", "variety", "family", "quantity", "harvested", "source", "notes"]


def rows(conn):
    for s in store.list_seeds(conn):
        yield [s.id, s.name, s.variety, s.family, s.quantity,
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
