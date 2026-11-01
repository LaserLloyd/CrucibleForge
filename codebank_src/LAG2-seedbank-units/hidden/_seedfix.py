import io
import json
import random
import sqlite3
from fractions import Fraction

V2_SCHEMA = """
CREATE TABLE seeds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    variety TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    harvested INTEGER,
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_seeds_name ON seeds(name);
CREATE TABLE sowings (
    id INTEGER PRIMARY KEY,
    seed_id INTEGER NOT NULL REFERENCES seeds(id) ON DELETE CASCADE,
    sown_on TEXT NOT NULL,
    bed TEXT NOT NULL DEFAULT '',
    count INTEGER
);
CREATE INDEX idx_sowings_seed ON sowings(seed_id);
"""
V1_SCHEMA = V2_SCHEMA.replace("    family TEXT NOT NULL DEFAULT '',\n", "")

TS = "2025-03-01T10:00:00+00:00"

# (id, name, quantity, notes)
V2_ROWS = [
    (1, "Cherokee Purple Tomato", "2 packets", ""),
    (2, "Kale", "  About 30 Seeds ", "from the swap"),
    (3, "Runner Bean", "1.5oz", ""),
    (4, "Parsnip", "half packet", ""),
    (5, "Lettuce", "", "nothing left"),
    (6, "Leek", "   ", ""),
    (7, "Squash", "2 Packets from Gran", ""),
    (8, "Chilli", " a handful ", "hot ones"),
    (9, "Carrot", "1.5", "line one\nline two"),
    (10, "Radish", "about ~3", ""),
    (11, "Beetroot", "~ half a dozen", ""),
    (12, "Pea", "0.5 OZ", ""),
    (13, "Sunflower", "1 1/2 pkts", ""),
    (14, "Coriander", "1/3 packet", ""),
    (15, "Dill", "1,25 g", ""),
    (16, "Onion", "a dozen seeds", ""),
    (20, "Chard", "1 kg", ""),
    (21, "Fennel", "2/0 packets", ""),
]
# id -> (amount_milli, unit, approx, notes)
V3_EXPECT = {
    1: (2000, "packet", 0, ""),
    2: (30000, "seed", 1, "from the swap"),
    3: (42524, "gram", 0, ""),
    4: (500, "packet", 0, ""),
    5: (None, None, 0, "nothing left"),
    6: (None, None, 0, ""),
    7: (None, None, 0, "[quantity: 2 Packets from Gran]"),
    8: (None, None, 0, "hot ones\n[quantity: a handful]"),
    9: (None, None, 0, "line one\nline two\n[quantity: 1.5]"),
    10: (None, None, 0, "[quantity: about ~3]"),
    11: (6000, "seed", 1, ""),
    12: (14175, "gram", 0, ""),
    13: (1500, "packet", 0, ""),
    14: (333, "packet", 0, ""),
    15: (1250, "gram", 0, ""),
    16: (12000, "seed", 0, ""),
    20: (1000000, "gram", 0, ""),
    21: (None, None, 0, "[quantity: 2/0 packets]"),
}
SOWINGS = [(1, 1, "2025-03-10", "A", 6), (2, 1, "2025-04-02", "B", 4), (3, 3, "2025-05-01", "", None),
           (4, 7, "2025-05-03", "C", 2), (5, 21, "2025-05-04", "", 1)]


def make_db(path, version=2, rows=V2_ROWS, sowings=SOWINGS, families=None):
    c = sqlite3.connect(path)
    c.executescript(V2_SCHEMA if version >= 2 else V1_SCHEMA)
    for sid, name, q, notes in rows:
        if version >= 2:
            fam = (families or {}).get(sid, "fam%d" % sid)
            c.execute("INSERT INTO seeds (id, name, variety, family, quantity, harvested, source, notes, created_at)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (sid, name, "v%d" % sid, fam, q, 2020 + sid % 5,
                                                             "src%d" % sid, notes, TS))
        else:
            c.execute("INSERT INTO seeds (id, name, variety, quantity, harvested, source, notes, created_at)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (sid, name, "v%d" % sid, q, 2020 + sid % 5,
                                                          "src%d" % sid, notes, TS))
    c.executemany("INSERT INTO sowings VALUES (?, ?, ?, ?, ?)", sowings)
    c.execute("PRAGMA user_version = %d" % version)
    c.commit()
    c.close()


def dump(path):
    c = sqlite3.connect(path)
    out = {"version": c.execute("PRAGMA user_version").fetchone()[0],
           "master": sorted(c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall(),
                            key=repr)}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        out[t] = sorted(c.execute("SELECT * FROM %s" % t).fetchall(), key=repr)
    c.close()
    return out


def cols(conn, table="seeds"):
    return {(r[1], r[2].upper(), r[3], r[4], r[5]) for r in conn.execute("PRAGMA table_info(%s)" % table)}


def triple(a):
    return None if a is None else (a.milli, a.unit, bool(a.approx))


def call(app, method, path, body=None, query=""):
    raw = b"" if body is None else json.dumps(body).encode()
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
           "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
    got = {}
    data = b"".join(app(env, lambda s, h: got.setdefault("s", s)))
    return int(got["s"].split()[0]), (json.loads(data) if data else None)


# ---------------------------------------------------------------- parser oracle
OZ = Fraction("28.349523125")
UNIT_WORDS = {"seed": ["seed", "seeds", ""], "packet": ["packet", "packets", "pkt", "pkts", "pack", "packs",
                                                         "sachet", "sachets"],
              "gram": ["g", "gram", "grams"], "kg": ["kg"], "oz": ["oz", "ounce", "ounces"]}
MARKERS = ["~", "~ ", "about ", "around ", "approx ", "approx. ", "ca. "]
WORDS = [("a", 1), ("an", 1), ("one", 1), ("two", 2), ("three", 3), ("seven", 7), ("ten", 10),
         ("half", Fraction(1, 2)), ("half a", Fraction(1, 2)), ("half an", Fraction(1, 2)),
         ("dozen", 12), ("a dozen", 12), ("half dozen", 6), ("half a dozen", 6)]


def _rhu(x):
    return int((x + Fraction(1, 2)) // 1)


def random_quantity(rng):
    """(text, expected (milli, unit, approx) or 'error')."""
    approx = rng.random() < 0.35
    pre = rng.choice(MARKERS) if approx else ""
    kind = rng.choice(["int", "dec", "frac", "mixed", "word"])
    digit = kind != "word"
    if kind == "int":
        v = Fraction(rng.randint(0, 400)); s = str(v)
    elif kind == "dec":
        a, b = rng.randint(0, 50), rng.randint(0, 9999)
        bs = str(b).zfill(rng.randint(len(str(b)), 4))
        s = "%d%s%s" % (a, rng.choice([".", ","]), bs); v = Fraction("%d.%s" % (a, bs))
    elif kind == "frac":
        n, d = rng.randint(0, 20), rng.randint(1, 12)
        s = "%d/%d" % (n, d); v = Fraction(n, d)
    elif kind == "mixed":
        w, n, d = rng.randint(0, 9), rng.randint(0, 11), rng.randint(1, 12)
        s = "%d %d/%d" % (w, n, d); v = w + Fraction(n, d)
    else:
        s, v = rng.choice(WORDS); v = Fraction(v)
    ukey = rng.choice(list(UNIT_WORDS))
    uw = rng.choice(UNIT_WORDS[ukey])
    unit, factor = {"seed": ("seed", 1), "packet": ("packet", 1), "gram": ("gram", 1), "kg": ("gram", 1000),
                    "oz": ("gram", OZ)}[ukey]
    if uw == "":
        text = pre + s
    elif digit and rng.random() < 0.3:
        text = pre + s + uw
    else:
        text = pre + s + " " + uw
    # random casing and whitespace
    text = "".join(ch.upper() if rng.random() < 0.3 else ch for ch in text)
    text = text.replace(" ", rng.choice([" ", "  ", " \t"]))
    text = rng.choice(["", " ", "\t"]) + text + rng.choice(["", " ", "  "])
    if unit == "seed" and v.denominator != 1:
        return text, "error"
    return text, (_rhu(v * factor * 1000), unit, approx)


def format_oracle(milli, unit, approx):
    w, f = divmod(milli, 1000)
    n = str(w) if not f else ("%d.%03d" % (w, f)).rstrip("0")
    u = "g" if unit == "gram" else (unit if milli == 1000 else unit + "s")
    return ("about " if approx else "") + n + " " + u
