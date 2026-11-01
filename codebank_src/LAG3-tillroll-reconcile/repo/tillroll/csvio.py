"""Reading the exports. The bank export is semicolon separated (the bank is
German), the till export comma separated."""
import csv
import io
from datetime import date, datetime

from . import money
from .models import BankLine, Receipt


def _day(text: str) -> date:
    text = text.strip()
    for f in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            pass
    raise ValueError(f"not a date: {text!r}")


def read_bank(text: str) -> list[BankLine]:
    """Columns: id;date;amount;memo (header row required)."""
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    out = []
    for row in rows:
        amount = row["amount"].strip().replace(".", "").replace(",", ".") if "," in row["amount"] else row["amount"]
        out.append(BankLine(int(row["id"]), _day(row["date"]), money.parse(amount), (row.get("memo") or "").strip()))
    return out


def read_receipts(text: str) -> list[Receipt]:
    """Columns: id,date,amount,till (header row required; till defaults to front)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(Receipt(int(row["id"]), _day(row["date"]), money.parse(row["amount"]),
                           (row.get("till") or "front").strip() or "front"))
    return out


def write_matches(result) -> str:
    """The match list the accountant imports: kind,bank_id,receipt_ids."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["kind", "bank_id", "receipt_ids"])
    for b, r in result.singles:
        w.writerow(["single", b, r])
    for b, r1, r2 in result.pairs:
        w.writerow(["pair", b, f"{r1}+{r2}"])
    return buf.getvalue()
