"""Human-friendly amounts."""


def amount(x: float) -> str:
    whole = int(x)
    frac = x - whole
    if frac == 0:
        return str(whole)
    for den in (2, 3, 4, 8):
        num = round(frac * den)
        if abs(frac - num / den) < 0.01:
            return f"{whole} {num}/{den}" if whole else f"{num}/{den}"
    return f"{x:.2f}"
