"""The ``seedbank`` command.

    seedbank [--db PATH] add NAME [--variety V] [--qty TEXT | --amount N --unit U [--approx]] [--harvested YEAR] [--source S]
    seedbank [--db PATH] list [--family F]
    seedbank [--db PATH] export FILE.csv
    seedbank [--db PATH] import FILE.csv
    seedbank [--db PATH] sow ID DATE [--bed B] [--count N]
    seedbank [--db PATH] check [--year YYYY]      viability of every seed
"""
import argparse
import os
import sys
from datetime import date

from . import db, export, store, viability
from . import quantity as _q

DEFAULT_DB = os.path.expanduser("~/.local/share/seedbank/seeds.db")


def _parser():
    p = argparse.ArgumentParser(prog="seedbank")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--variety", default="")
    a.add_argument("--qty", default=None)
    a.add_argument("--amount", default=None)
    a.add_argument("--unit", default=None)
    a.add_argument("--approx", action="store_true")
    a.add_argument("--harvested", default=None)
    a.add_argument("--source", default="")
    li = sub.add_parser("list")
    li.add_argument("--family", default=None)
    e = sub.add_parser("export")
    e.add_argument("file")
    im = sub.add_parser("import")
    im.add_argument("file")
    s = sub.add_parser("sow")
    s.add_argument("id", type=int)
    s.add_argument("date")
    s.add_argument("--bed", default="")
    s.add_argument("--count", type=int, default=None)
    c = sub.add_parser("check")
    c.add_argument("--year", type=int, default=None)
    return p


def _line(seed):
    return f"{seed.id:>4}  {seed.label()}  {seed.quantity or '-'}"


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.cmd == "add":
        if args.qty is not None and (args.amount is not None or args.unit is not None):
            parser.error("give --qty or --amount/--unit, not both")
        if (args.amount is None) != (args.unit is None):
            parser.error("--amount and --unit go together")
    if args.db != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = db.connect(args.db)
    try:
        if args.cmd == "add":
            if args.amount is not None:
                amt = {"amount": _q.make_amount(args.amount, args.unit, args.approx)}
            else:
                amt = {"quantity": args.qty or ""}
            s = store.add_seed(conn, args.name, variety=args.variety, harvested=args.harvested,
                               source=args.source, **amt)
            print(_line(s))
        elif args.cmd == "list":
            for s in store.list_seeds(conn, family=args.family):
                print(_line(s))
        elif args.cmd == "export":
            with open(args.file, "w", encoding="utf-8", newline="") as fh:
                n = export.export_csv(conn, fh)
            print(f"exported {n} seeds to {args.file}")
        elif args.cmd == "import":
            with open(args.file, encoding="utf-8", newline="") as fh:
                n = export.import_csv(conn, fh)
            print(f"imported {n} seeds")
        elif args.cmd == "sow":
            w = store.add_sowing(conn, args.id, args.date, args.bed, args.count)
            print(f"sowing {w.id}: seed {w.seed_id} on {w.sown_on}")
        elif args.cmd == "check":
            year = args.year or date.today().year
            for s in store.list_seeds(conn):
                print(f"{s.id:>4}  {s.label()}  {viability.status(s.family, s.harvested, year)}")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
