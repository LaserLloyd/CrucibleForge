"""CSV statement import (README "Import")."""
import csv
import io
from dataclasses import dataclass

from .dates import parse_date
from .money import parse_amount
from .rules import normalise_payee


@dataclass
class ImportResult:
    added: int
    skipped: int


def read_rows(text: str) -> list[dict]:
    """Rows of a statement as dicts with date/cents/payee/memo; header names are case-insensitive."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return []
    for need in ("date", "amount", "payee"):
        if need not in header:
            raise ValueError(f"statement has no {need!r} column")
    col = {name: i for i, name in enumerate(header)}
    rows = []
    for n, rec in enumerate(reader, 2):
        if not any(cell.strip() for cell in rec):
            continue
        try:
            rows.append({"date": parse_date(rec[col["date"]]),
                         "cents": parse_amount(rec[col["amount"]]),
                         "payee": rec[col["payee"]].strip(),
                         "memo": rec[col["memo"]].strip() if "memo" in col and col["memo"] < len(rec) else ""})
        except (ValueError, IndexError) as e:
            raise ValueError(f"statement line {n}: {e}") from None
    return rows


def import_csv(ledger, text: str) -> ImportResult:
    rows = read_rows(text)
    seen = set(ledger.key_counts(normalise_payee))
    added = skipped = 0
    for r in rows:
        key = (r["date"], r["cents"], normalise_payee(r["payee"]))
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        ledger.add(r["date"], r["cents"], r["payee"], memo=r["memo"])
        added += 1
    return ImportResult(added, skipped)
