"""VAT summary for the quarterly return. Bread and cakes are reduced rate
(7 %), drinks and everything else standard (19 %). Receipts carry gross
amounts; the tax inside a gross amount is gross * rate / (100 + rate),
rounded half up per receipt."""
from collections import defaultdict
from datetime import date

RATES = {"reduced": 7, "standard": 19}


def tax_in_gross(gross: int, rate: int) -> int:
    q, r = divmod(abs(gross) * rate, 100 + rate)
    if 2 * r >= 100 + rate:
        q += 1
    return q if gross >= 0 else -q


def quarter(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def summary(lines):
    """lines: iterable of (day, gross_cents, rate_name). Returns
    {quarter: {rate_name: {"gross": .., "tax": .., "net": ..}}}."""
    out = defaultdict(lambda: defaultdict(lambda: {"gross": 0, "tax": 0, "net": 0}))
    for day, gross, rate_name in lines:
        if rate_name not in RATES:
            raise ValueError(f"unknown VAT rate {rate_name!r}")
        tax = tax_in_gross(gross, RATES[rate_name])
        cell = out[quarter(day)][rate_name]
        cell["gross"] += gross
        cell["tax"] += tax
        cell["net"] += gross - tax
    return {q: dict(v) for q, v in sorted(out.items())}
