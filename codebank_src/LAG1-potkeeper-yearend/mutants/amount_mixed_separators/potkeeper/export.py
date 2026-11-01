"""CSV and JSON export (README "Export")."""
import csv
import io
import json

from .money import format_cents


def _plain(cents: int) -> str:
    return format_cents(cents).replace(",", "")


def to_csv(transactions) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "date", "amount", "payee", "category", "memo"])
    for t in transactions:
        w.writerow([t.id, t.date.isoformat(), _plain(t.cents), t.payee, t.category or "", t.memo])
    return buf.getvalue()


def to_json(transactions) -> str:
    rows = [{"id": t.id, "date": t.date.isoformat(), "amount": t.cents, "payee": t.payee,
             "category": t.category, "memo": t.memo, "shared": t.shared, "paid_by": t.paid_by,
             "tags": list(t.tags)} for t in transactions]
    return json.dumps(rows, indent=1, ensure_ascii=False)
