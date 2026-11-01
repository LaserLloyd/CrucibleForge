"""Human-friendly amounts."""
from fractions import Fraction


def amount(x: float) -> str:
    if x < 0.125:
        return "a pinch"
    fx = Fraction(x)
    w = int(x)
    cands = {Fraction(whole) + Fraction(n, d): d
             for d in (1, 2, 3, 4, 8) for whole in (w, w + 1) for n in range(d)}
    # an exact value keeps its smallest denominator
    for v in list(cands):
        cands[v] = min(d for d in (1, 2, 3, 4, 8) if (v * d).denominator == 1)
    v = min(cands, key=lambda c: (abs(fx - c), cands[c], c))
    whole, rest = int(v), v - int(v)
    if rest == 0:
        return str(whole)
    return f"{whole} {rest.numerator}/{rest.denominator}" if whole else f"{rest.numerator}/{rest.denominator}"
