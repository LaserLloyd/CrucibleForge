"""Text charts for the terminal: spending per category as horizontal bars."""
from .money import format_cents

FULL = "#"
WIDTH = 40


def bar(value: int, top: int, width: int = WIDTH) -> str:
    """A bar of `value` relative to `top` (the longest bar), at least one mark
    for any positive value, nothing for zero or less."""
    if value <= 0 or top <= 0:
        return ""
    n = value * width // top
    return FULL * max(1, n)


def category_chart(rows, width: int = WIDTH) -> str:
    """rows: [(category, spent)] as returned by the reports; refunds-only
    categories (spent <= 0) are listed without a bar."""
    if not rows:
        return "(no transactions)\n"
    top = max((s for _, s in rows), default=0)
    name_w = max(len(c) for c, _ in rows)
    lines = []
    for cat, spent in rows:
        lines.append(f"{cat:<{name_w}}  {format_cents(spent):>12}  {bar(spent, top, width)}".rstrip())
    return "\n".join(lines) + "\n"


def sparkline(values) -> str:
    """One character per value, scaled between the smallest and largest value."""
    ticks = " .:-=+*#"
    values = list(values)
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return ticks[len(ticks) // 2] * len(values)
    return "".join(ticks[(v - lo) * (len(ticks) - 1) // (hi - lo)] for v in values)
