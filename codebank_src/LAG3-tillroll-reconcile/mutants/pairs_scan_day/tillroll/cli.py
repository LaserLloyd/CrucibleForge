"""python -m tillroll.cli reconcile BANK.csv RECEIPTS.csv [--matches OUT.csv]"""
import argparse
import sys
import time

from . import csvio
from .reconcile import reconcile
from .report import monthly_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tillroll")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("reconcile")
    rc.add_argument("bank")
    rc.add_argument("receipts")
    rc.add_argument("--matches")
    a = ap.parse_args(argv)
    with open(a.bank, encoding="utf-8") as f:
        bank = csvio.read_bank(f.read())
    with open(a.receipts, encoding="utf-8") as f:
        receipts = csvio.read_receipts(f.read())
    t0 = time.monotonic()
    result = reconcile(bank, receipts)
    t1 = time.monotonic()
    sys.stdout.write(monthly_report(bank, receipts, result))
    print(f"reconcile {t1 - t0:.1f}s, report {time.monotonic() - t1:.1f}s", file=sys.stderr)
    if a.matches:
        with open(a.matches, "w", encoding="utf-8") as f:
            f.write(csvio.write_matches(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
