"""Plain-text rota table."""


def render(rows):
    """rows: [(week, {chore: member or None})] -> text table."""
    if not rows:
        return ""
    chores = list(rows[0][1])
    width = max(len(c) for c in chores + ["week"])
    lines = ["week".ljust(width) + " | " + " | ".join(str(w) for w, _ in rows)]
    for c in chores:
        lines.append(c.ljust(width) + " | " + " | ".join((a[c] or "-") for _, a in rows))
    return "\n".join(lines)
