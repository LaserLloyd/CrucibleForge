"""Units: temperature and energy."""


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9


def parse_temperature(text: str) -> float:
    """'21.5C', '70F', '21' (Celsius) -> degrees Celsius."""
    t = text.strip().upper().replace("°", "")
    if t.endswith("F"):
        return round(f_to_c(float(t[:-1])), 2)
    if t.endswith("C"):
        t = t[:-1]
    return float(t)


def parse_energy(text: str) -> int:
    """'1.5kWh', '250Wh', '250' (Wh) -> integer Wh (kWh rounded to the nearest Wh)."""
    t = text.strip().lower().replace(" ", "")
    if t.endswith("kwh"):
        return round(float(t[:-3]) * 1000)
    if t.endswith("wh"):
        t = t[:-2]
    return int(t)
