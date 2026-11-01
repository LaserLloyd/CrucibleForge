"""Bank statement import.

Turns CSV exports into ledger transactions and merges them into the ledger.
Each bank has its own format; see BANK_FORMATS. All money is integer cents.
"""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

DUP_DAYS = 2


@dataclass(frozen=True)
class Txn:
    date: date
    amount_cents: int
    payee: str
    account: str
    category: str = "uncategorised"


@dataclass
class ImportResult:
    rows: list = field(default_factory=list)
    skipped: int = 0
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------- payees
_PAYEE_NOISE = re.compile(r"\b(sagt danke|gmbh|ltd|inc|kk|co)\b|\*+|\d{4,}", re.I)
_SPACES = re.compile(r"\s+")


def normalize_payee(raw: str) -> str:
    """Payee for matching and display:
    * Unicode NFKC, then casefold;
    * card-terminal noise removed: runs of '*', numbers of 4+ digits, and the
      words 'sagt danke', 'gmbh', 'ltd', 'inc', 'kk', 'co';
    * spaces collapsed, ends stripped, then title-cased with str.title().
    'REWE SAGT DANKE 4711*0815' -> 'Rewe'."""
    s = unicodedata.normalize("NFKC", raw).casefold()
    s = _PAYEE_NOISE.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    return s.title()


# --------------------------------------------------------------- amounts
def parse_amount_us(text: str) -> int:
    """'1,234.56', '-12.50', '(12.50)' (negative), '12' -> cents."""
    t = text.strip().replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    if t.startswith("-"):
        neg, t = True, t[1:]
    whole, _, frac = t.partition(".")
    if not whole.isdigit() or (frac and (not frac.isdigit() or len(frac) > 2)):
        raise ValueError(f"bad amount {text!r}")
    cents = int(whole) * 100 + int(frac.ljust(2, "0") or 0)
    return -cents if neg else cents


def parse_amount_eu(text: str) -> int:
    """'1.234,56', '-12,50', '12' -> cents (German style)."""
    t = text.strip().replace(".", "").replace(",", ".")
    return parse_amount_us(t)


# ----------------------------------------------------------------- dates
def parse_date(text: str, fmt: str) -> date:
    return datetime.strptime(text.strip(), fmt).date()


# --------------------------------------------------------------- formats
BANK_FORMATS = {
    # "harbor": Harbor Credit Union, US checking. Header row, comma separated:
    #   Date,Description,Amount   date MM/DD/YYYY, amount US style, money out negative
    "harbor": {"delimiter": ",", "header": True, "date": "%m/%d/%Y"},
    # "kiwi": Kiwi Card, credit card. No header, comma separated:
    #   YYYY-MM-DD,payee,debit,credit   one of debit/credit is empty; debit = money out
    "kiwi": {"delimiter": ",", "header": False, "date": "%Y-%m-%d"},
    # "alpen": Alpenbank savings. Header row, ';' separated:
    #   Datum;Text;Soll;Haben   date DD.MM.YY, amounts German style, Soll = money out
    "alpen": {"delimiter": ";", "header": True, "date": "%d.%m.%y"},
    # "nordbank": Nordbank joint account. Header row, ';' separated:
    #   Buchungstag;Wertstellung;Empfaenger;Verwendungszweck;Betrag;Waehrung
    #   dates DD.MM.YYYY (Buchungstag is used), Betrag German style, signed;
    #   rows in another currency than EUR are skipped (counted in `skipped`)
    "nordbank": {"delimiter": ";", "header": True, "date": "%d.%m.%Y"},
}


def _rows(text: str, fmt: dict):
    reader = csv.reader(io.StringIO(text), delimiter=fmt["delimiter"])
    rows = [r for r in reader if any(c.strip() for c in r)]
    return rows[1:] if fmt["header"] else rows


def _harbor(row, fmt):
    d, payee, amount = row
    return parse_date(d, fmt["date"]), payee, parse_amount_us(amount)


def _kiwi(row, fmt):
    d, payee, debit, credit = row
    if debit.strip():
        cents = -parse_amount_us(debit)
    else:
        cents = parse_amount_us(credit)
    return parse_date(d, fmt["date"]), payee, cents


def _alpen(row, fmt):
    d, text, soll, haben = row
    if soll.strip():
        cents = -parse_amount_eu(soll)
    else:
        cents = parse_amount_eu(haben)
    return parse_date(d, fmt["date"]), text, cents


class _SkipRow(Exception):
    """The row is valid but not for this ledger (counted in `skipped`)."""


def _nordbank(row, fmt):
    buchungstag, _wertstellung, empfaenger, _zweck, betrag, waehrung = row
    if waehrung.strip() != "EUR":
        raise _SkipRow(waehrung)
    return parse_date(_wertstellung, fmt["date"]), empfaenger, parse_amount_eu(betrag)


_PARSERS = {"harbor": _harbor, "kiwi": _kiwi, "alpen": _alpen, "nordbank": _nordbank}


def import_csv(text: str, bank: str, account: str) -> ImportResult:
    """Parse one export. Bad rows are reported in `errors` as
    '<line number>: <reason>' (line numbers count the header) and skipped."""
    if bank not in BANK_FORMATS:
        raise ValueError(f"unknown bank {bank!r}")
    fmt = BANK_FORMATS[bank]
    result = ImportResult()
    offset = 2 if fmt["header"] else 1
    for i, row in enumerate(_rows(text, fmt)):
        try:
            d, payee, cents = _PARSERS[bank](row, fmt)
        except _SkipRow:
            result.skipped += 1
            continue
        except (ValueError, TypeError) as e:
            result.errors.append(f"{i + offset}: {e}")
            continue
        p = normalize_payee(payee)
        result.rows.append(Txn(d, cents, p, account, categorize(p, cents)))
    return result


# ------------------------------------------------------------ categories
RULES = [
    # (payee substring, lower-case; sign: "out"/"in"/"any"; category)
    ("rewe", "out", "groceries"),
    ("edeka", "out", "groceries"),
    ("aldi", "out", "groceries"),
    ("stadtwerke", "out", "utilities"),
    ("netflix", "out", "subscriptions"),
    ("spotify", "out", "subscriptions"),
    ("salary", "in", "income"),
    ("gehalt", "in", "income"),
    ("refund", "in", "refunds"),
    ("db vertrieb", "out", "travel"),
    ("shell", "out", "car"),
]


def categorize(payee: str, cents: int) -> str:
    p = payee.lower()
    for needle, sign, cat in RULES:
        if needle in p and (sign == "any" or (sign == "out") == (cents < 0)):
            return cat
    return "uncategorised"


# ----------------------------------------------------------------- merge
def is_duplicate(a: Txn, b: Txn) -> bool:
    return (a.account == b.account and a.amount_cents == b.amount_cents
            and normalize_payee(a.payee) == normalize_payee(b.payee)
            and abs((a.date - b.date).days) <= DUP_DAYS)


def merge(ledger: list, new: list) -> list:
    """Add new rows that are not duplicates of ledger rows (see README). Returns the added rows."""
    before = list(ledger)
    used = [False] * len(before)
    added = []
    for t in new:
        for i, old in enumerate(before):
            if not used[i] and is_duplicate(t, old):
                used[i] = True
                break
        else:
            ledger.append(t)
            added.append(t)
    return added


# ---------------------------------------------------------------- report
def monthly_totals(ledger: list) -> dict:
    """{'YYYY-MM': {category: cents}} over the whole ledger."""
    out: dict = {}
    for t in ledger:
        m = t.date.strftime("%Y-%m")
        out.setdefault(m, {})
        out[m][t.category] = out[m].get(t.category, 0) + t.amount_cents
    return out


# ------------------------------------------------------------ budgets
BUDGETS = {
    # category: monthly limit in cents (money out, as a positive number)
    "groceries": 60000,
    "subscriptions": 4000,
    "car": 15000,
    "travel": 20000,
}


def budget_alerts(ledger: list, month: str) -> list:
    """Categories over budget in `month` ('YYYY-MM'), as
    [(category, spent_cents, limit_cents)] sorted by how far over, most first.
    Money in (refunds) reduces what was spent."""
    totals = monthly_totals(ledger).get(month, {})
    out = []
    for cat, limit in BUDGETS.items():
        spent = -totals.get(cat, 0)
        if spent > limit:
            out.append((cat, spent, limit))
    return sorted(out, key=lambda x: (-(x[1] - x[2]), x[0]))


# ------------------------------------------------------------ QIF export
def _qif_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def export_qif(rows: list, account_type: str = "Bank") -> str:
    """Quicken Interchange Format for the accountant's software:
    a '!Type:<account_type>' header, then per row D(date MM/DD'YY), T(amount),
    P(payee), L(category, omitted when uncategorised) and '^'."""
    lines = [f"!Type:{account_type}"]
    for t in rows:
        lines.append("D" + t.date.strftime("%m/%d'%y"))
        lines.append("T" + _qif_amount(t.amount_cents))
        lines.append("P" + t.payee)
        if t.category != "uncategorised":
            lines.append("L" + t.category)
        lines.append("^")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- ledger storage
LEDGER_HEADER = ["date", "amount_cents", "payee", "account", "category"]


def save_ledger(path, ledger: list) -> None:
    """The ledger as CSV (UTF-8, header row, ISO dates), sorted by date, account, payee."""
    rows = sorted(ledger, key=lambda t: (t.date, t.account, t.payee, t.amount_cents))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(LEDGER_HEADER)
        for t in rows:
            w.writerow([t.date.isoformat(), t.amount_cents, t.payee, t.account, t.category])


def load_ledger(path) -> list:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        header = next(r, None)
        if header != LEDGER_HEADER:
            raise ValueError(f"not a ledger file: {path}")
        for row in r:
            d, cents, payee, account, category = row
            out.append(Txn(date.fromisoformat(d), int(cents), payee, account, category))
    return out


# ------------------------------------------------------------ recurring
def recurring(ledger: list, min_months: int = 3) -> list:
    """Payees charged in at least `min_months` different months with the same
    amount, as [(payee, amount_cents, months)] sorted by payee: subscriptions
    the family may have forgotten about."""
    seen: dict = {}
    for t in ledger:
        if t.amount_cents >= 0:
            continue
        seen.setdefault((t.payee, t.amount_cents), set()).add(t.date.strftime("%Y-%m"))
    out = [(p, c, len(m)) for (p, c), m in seen.items() if len(m) >= min_months]
    return sorted(out)


# ----------------------------------------------------------------- CLI
def main(argv=None) -> int:
    """stmtimport.py LEDGER.csv BANK ACCOUNT EXPORT.csv [--dry-run]"""
    import sys
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 4:
        print(main.__doc__, file=sys.stderr)
        return 2
    ledger_path, bank, account, export_path = args
    try:
        ledger = load_ledger(ledger_path)
    except FileNotFoundError:
        ledger = []
    with open(export_path, encoding="utf-8-sig") as fh:
        result = import_csv(fh.read(), bank, account)
    added = merge(ledger, result.rows)
    for e in result.errors:
        print(f"warning: {export_path}:{e}", file=sys.stderr)
    print(f"{len(added)} added, {len(result.rows) - len(added)} duplicates, "
          f"{result.skipped} skipped, {len(result.errors)} errors")
    if not dry:
        save_ledger(ledger_path, ledger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
