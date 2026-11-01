"""Structured amounts (docs/AMOUNTS.md): parse free text, format for people."""
import re
from fractions import Fraction
from typing import NamedTuple

UNITS = ("seed", "packet", "gram")


class QuantityError(ValueError):
    pass


class Amount(NamedTuple):
    milli: int
    unit: str
    approx: bool = False


_MARKERS = ("about ", "around ", "approx. ", "approx ", "ca. ")
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "half": Fraction(1, 2), "half a": Fraction(1, 2), "half an": Fraction(1, 2),
          "dozen": 12, "a dozen": 12, "half dozen": 6, "half a dozen": 6}
_UNITS = {"": ("seed", 1), "seed": ("seed", 1), "seeds": ("seed", 1),
          "packet": ("packet", 1), "packets": ("packet", 1), "pkt": ("packet", 1), "pkts": ("packet", 1),
          "pack": ("packet", 1), "packs": ("packet", 1), "sachet": ("packet", 1), "sachets": ("packet", 1),
          "g": ("gram", 1), "gram": ("gram", 1), "grams": ("gram", 1), "kg": ("gram", 1000),
          "oz": ("gram", Fraction("28.349523125")), "ounce": ("gram", Fraction("28.349523125")),
          "ounces": ("gram", Fraction("28.349523125"))}
_DIGITS = re.compile(r"(\d+) (\d+)/(\d+)|(\d+)/(\d+)|(\d+)[.,](\d+)|(\d+)")


def round_half_up(x):
    """Nearest integer of a non-negative Fraction, halves up."""
    return int((x + Fraction(1, 2)) // 1)


def _number(t):
    """(value, rest, is_digit_form) for the number at the start of t."""
    m = _DIGITS.match(t)
    if m:
        if m.group(1) is not None:
            if int(m.group(3)) == 0:
                raise ZeroDivisionError
            v = int(m.group(1)) + Fraction(int(m.group(2)), int(m.group(3)))
        elif m.group(4) is not None:
            if int(m.group(5)) == 0:
                raise ZeroDivisionError
            v = Fraction(int(m.group(4)), int(m.group(5)))
        elif m.group(6) is not None:
            v = Fraction(f"{m.group(6)}.{m.group(7)}")
        else:
            v = Fraction(int(m.group(8)))
        return v, t[m.end():], True
    for w in sorted(_WORDS, key=len, reverse=True):
        if t == w or t.startswith(w + " "):
            return Fraction(_WORDS[w]), t[len(w):], False
    return None, t, False


def parse_quantity(text):
    if not isinstance(text, str):
        raise QuantityError(f"quantity must be text: {text!r}")
    t = " ".join(text.lower().split())
    if not t:
        return None
    approx = False
    if t.startswith("~"):
        approx, t = True, t[1:].lstrip(" ")
    else:
        for mk in _MARKERS:
            if t.startswith(mk):
                approx, t = True, t[len(mk):]
                break
    try:
        value, rest, digits = _number(t)
    except ZeroDivisionError:
        raise QuantityError(f"cannot read quantity {text!r}: division by zero") from None
    if value is None:
        raise QuantityError(f"cannot read quantity {text!r}")
    if rest.startswith(" "):
        rest = rest[1:]
    elif rest and not digits:
        raise QuantityError(f"cannot read quantity {text!r}")
    if rest not in _UNITS:
        raise QuantityError(f"cannot read quantity {text!r}: unknown unit {rest!r}")
    unit, factor = _UNITS[rest]
    if unit == "seed" and value.denominator != 1:
        raise QuantityError(f"cannot read quantity {text!r}: seeds are whole")
    return Amount(round_half_up(value * factor * 1000), unit, approx)


def format_number(milli):
    whole, frac = divmod(milli, 1000)
    if not frac:
        return str(whole)
    return f"{whole}.{frac:03d}".rstrip("0")


def format_amount(amount):
    if amount is None:
        return ""
    n = format_number(amount.milli)
    if amount.unit == "gram":
        u = "g"
    else:
        u = amount.unit if amount.milli == 1000 else amount.unit + "s"
    return ("about " if amount.approx else "") + f"{n} {u}"


def parse_value(v):
    """API value: a JSON number or a plain decimal string -> exact Fraction (>= 0)."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise QuantityError(f"amount value must be a number: {v!r}")
    s = repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, float) and ("e" in s or "inf" in s or "nan" in s):
        raise QuantityError(f"amount value must be a plain number: {v!r}")
    if not re.fullmatch(r"\d+(\.\d+)?", s.strip()):
        raise QuantityError(f"amount value must be a plain non-negative number: {v!r}")
    return Fraction(s.strip())


def make_amount(value, unit, approx=False):
    """Amount from an API/CLI value + unit (same rounding and seed rule as the parser)."""
    if unit not in UNITS:
        raise QuantityError(f"unknown unit {unit!r}")
    if not isinstance(approx, bool):
        raise QuantityError("approx must be true or false")
    x = parse_value(value)
    if unit == "seed" and x.denominator != 1:
        raise QuantityError("seeds are whole")
    return Amount(round_half_up(x * 1000), unit, approx)
