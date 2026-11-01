"""Create / read / update / delete for seeds and sowings.

All functions take a connection from ``db.connect`` and commit their own
changes. Validation errors raise ``ValueError`` with a message for the user.
"""
from datetime import date

from . import util, viability
from .models import Seed, Sowing

#: columns a caller may set on a seed (besides name)
EDITABLE = ("variety", "family", "quantity", "harvested", "source", "notes")


def _clean(fields):
    out = {}
    for k, v in fields.items():
        if k not in EDITABLE and k != "name":
            raise ValueError(f"unknown field {k!r}")
        if k == "harvested":
            out[k] = util.year_or_none(v)
        elif k == "name":
            if not isinstance(v, str) or not v.strip():
                raise ValueError("name is required")
            out[k] = v.strip()
        else:
            if v is None:
                v = ""
            if not isinstance(v, str):
                raise ValueError(f"{k} must be text")
            out[k] = v.strip() if k != "notes" else v
    return out


def add_seed(conn, name, **fields):
    data = _clean({"name": name, **fields})
    if "family" not in data:
        data["family"] = viability.family_of(data["name"])
    data["created_at"] = util.now_iso()
    cols = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO seeds ({cols}) VALUES ({marks})", tuple(data.values()))
    conn.commit()
    return get_seed(conn, cur.lastrowid)


def get_seed(conn, seed_id):
    row = conn.execute("SELECT * FROM seeds WHERE id = ?", (seed_id,)).fetchone()
    return Seed.from_row(row) if row else None


def list_seeds(conn, family=None):
    if family is None:
        rows = conn.execute("SELECT * FROM seeds ORDER BY name, variety, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM seeds WHERE family = ? ORDER BY name, variety, id",
                            (family,)).fetchall()
    return [Seed.from_row(r) for r in rows]


def update_seed(conn, seed_id, **fields):
    data = _clean(fields)
    if not data:
        return get_seed(conn, seed_id)
    sets = ", ".join(f"{k} = ?" for k in data)
    cur = conn.execute(f"UPDATE seeds SET {sets} WHERE id = ?", (*data.values(), seed_id))
    conn.commit()
    return get_seed(conn, seed_id) if cur.rowcount else None


def delete_seed(conn, seed_id):
    cur = conn.execute("DELETE FROM seeds WHERE id = ?", (seed_id,))
    conn.commit()
    return cur.rowcount > 0


def add_sowing(conn, seed_id, sown_on, bed="", count=None):
    if get_seed(conn, seed_id) is None:
        raise ValueError("no such seed")
    date.fromisoformat(sown_on)
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise ValueError("count must be a whole number")
    cur = conn.execute("INSERT INTO sowings (seed_id, sown_on, bed, count) VALUES (?, ?, ?, ?)",
                       (seed_id, sown_on, bed or "", count))
    conn.commit()
    return Sowing.from_row(conn.execute("SELECT * FROM sowings WHERE id = ?", (cur.lastrowid,)).fetchone())


def sowings_for(conn, seed_id):
    rows = conn.execute("SELECT * FROM sowings WHERE seed_id = ? ORDER BY sown_on, id", (seed_id,)).fetchall()
    return [Sowing.from_row(r) for r in rows]


def families(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT family FROM seeds ORDER BY family")]
