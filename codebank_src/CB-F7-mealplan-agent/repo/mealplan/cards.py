"""Printable recipe cards."""
from .fmt import amount


def card(recipe) -> str:
    lines = [f"{recipe['name']} (serves {recipe['servings']})"]
    for name, qty, unit in recipe["items"]:
        lines.append(f"- {name.strip()}" if unit == "to taste" else f"- {amount(qty)} {unit} {name.strip()}")
    return "\n".join(lines)
