"""Units of measure."""
from fractions import Fraction

# unit -> (family, factor to the family's base unit)
UNITS = {
    "g": ("mass", Fraction(1)),
    "kg": ("mass", Fraction(1000)),
    "ml": ("volume", Fraction(1)),
    "l": ("volume", Fraction(1000)),
    "tsp": ("volume", Fraction(5)),
    "tbsp": ("volume", Fraction(15)),
    "cup": ("volume", Fraction(240)),
    "pcs": ("count", Fraction(1)),
}


def normalize(unit: str) -> str:
    u = unit.strip().lower()
    if u not in UNITS:
        raise ValueError(f"unknown unit {unit!r}")
    return u


def family(unit: str) -> str:
    return UNITS[normalize(unit)][0]


def convert(qty, from_unit: str, to_unit: str) -> float:
    """Convert qty between two units of the same family."""
    f, t = normalize(from_unit), normalize(to_unit)
    base = Fraction(qty) * UNITS[f][1]
    return float(base // UNITS[t][1])
