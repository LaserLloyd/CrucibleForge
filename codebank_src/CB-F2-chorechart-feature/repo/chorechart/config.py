"""Reading the rota file:

    members: Ana, Ben, Cy
    chores: dishes, bins, laundry
"""


def load(path):
    members, chores = [], []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(":")
        names = [v.strip() for v in value.split(",") if v.strip()]
        if key.strip().lower() == "members":
            members = names
        elif key.strip().lower() == "chores":
            chores = names
        else:
            raise ValueError(f"unknown line: {line!r}")
    return members, chores
