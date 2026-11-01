"""Create / read / update / delete for seeds and sowings.

All functions take a connection from ``db.connect`` and commit their own
changes. Validation errors raise ``ValueError`` with a message for the user.
"""
from datetime import date

from . import quantity as _q
from . import util, viability
from .models import Seed, Sowing

#: columns a caller may set on a seed (besides name)
EDITABLE = ("variety", "family", "harvested", "source", "notes")
#: amount fields: amount= (an Amount or None) or quantity= (text, parsed)
AMOUNT_KEYS = ("amount", "quantity")


def _amount_cols(fields):
    """Pop amount=/quantity= from fields -> column dict (or {} when neither is given)."""
    given = [k for k in AMOUNT_KEYS if k in fields]
    if len(given) == 2:
        raise ValueError("give amount or quantity, not both")
    if not given:
        return {}
    v = fields.pop(given[0])
    if given[0] == "quantity":
        if v is None:
            v = ""
        if not isinstance(v, str):
            raise ValueError("quantity must be text")
        v = _q.parse_quantity(v)
    elif v is not None and not isinstance(v, _q.Amount):
        raise ValueError("amount must be an Amount")
    if v is None:
        return {"amount_milli": None, "unit": None, "approx": 0}
    return {"amount_milli": v.milli, "unit": v.unit, "approx": int(v.approx)}


def _clean(fields):
    fields = dict(fields)
    out = _amount_cols(fields)
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


def list_seeds(conn, family=None, unit=None):
    where, args = [], []
    if family is not None:
        where.append("family = ?")
        args.append(family)
    if unit is not None:
        if unit not in _q.UNITS:
            raise ValueError(f"unknown unit {unit!r}")
        where.append("unit = ?")
        args.append(unit)
    sql = "SELECT * FROM seeds" + (" WHERE " + " AND ".join(where) if where else "")
    rows = conn.execute(sql + " ORDER BY name, variety, id", args).fetchall()
    return [Seed.from_row(r) for r in rows]


def totals(conn):
    """{unit: (sum of milli, any approx)} over seeds that have an amount."""
    out = {}
    for unit, total, anyapprox in conn.execute(
            "SELECT unit, sum(amount_milli), max(approx) FROM seeds WHERE unit IS NOT NULL GROUP BY unit"):
        out[unit] = (total, bool(anyapprox))
    return out


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


class Conflict(ValueError):
    """The request is valid but clashes with the stored state (HTTP 409)."""


def add_sowing(conn, seed_id, sown_on, bed="", count=None, used=None):
    """Save a sowing; ``used`` (an Amount) is taken off the seed's stock in the same commit."""
    seed = get_seed(conn, seed_id)
    if seed is None:
        raise ValueError("no such seed")
    date.fromisoformat(sown_on)
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise ValueError("count must be a whole number")
    cur = conn.execute("INSERT INTO sowings (seed_id, sown_on, bed, count) VALUES (?, ?, ?, ?)",
                       (seed_id, sown_on, bed or "", count))
    conn.commit()
    if used is not None:
        if seed.amount is None:
            raise Conflict("seed has no amount")
        if used.unit != seed.amount.unit:
            raise Conflict("unit mismatch")
        if used.milli > seed.amount.milli:
            raise Conflict("not enough seed")
    try:
        if used is not None:
            conn.execute("UPDATE seeds SET amount_milli = amount_milli - ? WHERE id = ?", (used.milli, seed_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return Sowing.from_row(conn.execute("SELECT * FROM sowings WHERE id = ?", (cur.lastrowid,)).fetchone())


def sowings_for(conn, seed_id):
    rows = conn.execute("SELECT * FROM sowings WHERE seed_id = ? ORDER BY sown_on, id", (seed_id,)).fetchall()
    return [Sowing.from_row(r) for r in rows]


def families(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT family FROM seeds ORDER BY family")]
