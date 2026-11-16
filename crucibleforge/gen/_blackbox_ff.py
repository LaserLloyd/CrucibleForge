"""Helper of gen/blackbox.py: the fast-fail question BLACK BOX (ff/q/blackbox.py, branch t-blackbox @ 86e63b1) with the ff sandbox
runner removed (the engine grader runs the model's decoder). Generation, prompt, reference and baseline are unchanged, so
``ffseed: N`` reproduces the ff instance byte for byte. ``score_checks`` is new: the same ladder as ``score`` split into named
checks. Pinned by ``_FF_SHA`` in blackbox.py (chains.revision digests only the family file).
"""
import copy, json, random

NAME, TIERS, CPU_S = "blackbox", (1, 2, 3, 4), 5
POLYS = [0x07, 0x31, 0x1D, 0x2F, 0x9B, 0xD5, 0x8C]
MODES = ["IDLE", "ARMED", "TAKEOFF", "CRUISE", "HOVER", "LAND", "RTH", "FAULT", "ORBIT"]
TAGS = ["WP07", "RTH", "LOWB", "GEOF", "SURVEY", "PARK", "ALPHA", "HOME"]

POOL = {  # name: unit, description, signed, scale choices, width, physical lo, hi, step
    "alt_m": ("m", "altitude above the launch point", False, [1, 10], 2, 0, 3000, 25),
    "press_hpa": ("hPa", "barometric pressure", False, [10], 2, 960, 1040, 2),
    "heading_deg": ("deg", "compass heading", False, [10], 2, 0, 359, 15),
    "rpm": ("rpm", "main rotor speed", False, [1], 2, 0, 9000, 300),
    "speed_ms": ("m/s", "ground speed", False, [100], 2, 0, 30, 1.5),
    "batt_v": ("V", "battery voltage", False, [100, 1000], 2, 10.5, 16.8, 0.1),
    "temp_c": ("C", "board temperature", True, [10, 16], 2, -20, 60, 2),
    "pitch_deg": ("deg", "pitch angle", True, [100], 2, -40, 40, 3),
    "vspeed_ms": ("m/s", "vertical speed (up positive)", True, [100], 2, -12, 12, 1),
    "thr_pct": ("%", "throttle", False, [1], 1, 0, 100, 8),
    "rssi_dbm": ("dBm", "radio signal strength", True, [1], 1, -100, -35, 4),
}


# ---------------------------------------------------------------- schema
def _plain(name, rng, w=None, enc="uint"):
    unit, desc, sg, scs, pw, lo, hi, step = POOL[name]
    sc = rng.choice(scs)
    w = w or pw
    return dict(name=name, enc=enc, w=w, endian=rng.choice(["little", "big"]) if w > 1 else "little", signed=sg, scale=sc,
                lo=round(lo * sc), hi=round(hi * sc), step=max(1, round(step * sc)), unit=unit, desc=desc)


def _seq(rng):
    return dict(name="seq", enc="uint", w=rng.choice([1, 2]), endian=rng.choice(["little", "big"]), signed=False, scale=1, lo=0,
                hi=250, step=1, unit="", desc="frame counter (increments by one for every frame the recorder transmits, "
                                              "including frames lost to damage)", seq=True)


def _enum(rng, name="mode", desc="flight mode", n=6):
    return dict(name=name, enc="enum", names=rng.sample(MODES, n), unit="", desc=desc)


def _bits(rng, members):
    ms = []
    for nm, kind, nb, desc in members:
        ms.append(dict(name=nm, kind=kind, nbits=nb, desc=desc))
    order = rng.choice(["lsb", "msb"])
    tot = sum(m["nbits"] for m in ms)
    pos = 0 if order == "lsb" else 8 - tot if tot < 8 else 0
    # packed with a random gap-free start offset
    pos = rng.randint(0, 8 - tot)
    cur = pos
    for m in (ms if order == "lsb" else ms[::-1]):
        m["shift"] = cur
        cur += m["nbits"]
    return dict(name="_flags", enc="bits", members=ms, unit="", desc="packed flags")


MEMBERS = [("gps_lock", "bool", 1, "GPS fix present"), ("armed", "bool", 1, "motors armed"),
           ("link_q", "uint", 3, "radio link quality 0 (worst) to 7 (best)")]


def gen_schema(seed, tier):
    rng = random.Random(f"blackbox:schema:{seed}:{tier}")
    sync = rng.sample(range(0x80, 0x100), 2)
    S = {"tier": tier, "sync": sync, "has_type": tier == 3}
    S["len_mode"] = None if tier in (1, 4) else rng.choice(["p", "pc"] if tier == 2 else ["p", "tp", "tpc"])
    S["chk"] = {"kind": rng.choice(["sum8", "xor8", "crc8"]), "poly": rng.choice(POLYS),
                "span": "payload" if tier == 1 else rng.choice(["payload", "body"])}
    if tier == 4:  # pocket v2: 6 fields, wire order hidden (listing is a different shuffle), 3 decoded examples
        sq = _seq(rng)
        a = _plain(rng.choice(["alt_m", "press_hpa", "heading_deg", "rpm"]), rng)
        b = _plain(rng.choice(["batt_v", "speed_ms"]), rng)
        c = _plain(rng.choice(["temp_c", "pitch_deg", "vspeed_ms"]), rng)
        d = _plain(rng.choice(["thr_pct", "rssi_dbm"]), rng)
        e = _enum(rng)
        f = [sq, a, b, c, d, e]
        rng.shuffle(f)                       # wire order
        S["types"] = [{"name": "frame", "code": 0, "fields": f}]
        lst = [x for x in f]
        rng.shuffle(lst)                     # listing order (independent shuffle)
        if [x["name"] for x in lst] == [x["name"] for x in f]:
            lst = lst[1:] + lst[:1]
        S["list_order"] = [x["name"] for x in lst]
    elif tier == 1:
        f = [_seq(rng), _plain(rng.choice(["alt_m", "press_hpa", "heading_deg", "rpm"]), rng),
             _plain(rng.choice(["batt_v", "speed_ms"]), rng), _plain(rng.choice(["temp_c", "pitch_deg", "vspeed_ms"]), rng),
             _enum(rng)]
        S["types"] = [{"name": "frame", "code": 0, "fields": f}]
    elif tier == 2:
        sq = _seq(rng)
        a = _plain(rng.choice(["alt_m", "press_hpa", "heading_deg"]), rng)
        b = _plain(rng.choice(["batt_v", "speed_ms"]), rng)
        c = _plain(rng.choice(["temp_c", "pitch_deg", "vspeed_ms"]), rng)
        odo = dict(name="odom_m", enc="varint", scale=1, lo=0, hi=90000, step=40, unit="m", desc="distance flown since power-up",
                   mono=True)
        f = [sq, a, b, c, _enum(rng), odo, _bits(rng, MEMBERS)]
        S["types"] = [{"name": "frame", "code": 0, "fields": f}]
    else:
        codes = rng.sample(range(1, 0x7F), 2)
        tick = dict(name="tick_ms", enc="delta", w=2, endian=rng.choice(["little", "big"]), signed=False, scale=1,
                    lo=0, hi=0, step=100, unit="ms", desc="time since power-up", tick=True)
        alt = _plain("alt_m", rng, w=rng.choice([1, 2]), enc="delta")
        alt.update(scale=10, signed=True, step=16, lo=0, hi=30000, start_hi=40)
        batt = _plain("batt_v", rng)
        temp = _plain("temp_c", rng)
        odo = dict(name="odom_m", enc="varint", scale=1, lo=0, hi=90000, step=40, unit="m", desc="distance flown since power-up",
                   mono=True)
        fa = [_seq(rng), tick, alt, batt, temp, odo, _bits(rng, MEMBERS)]
        tag = dict(name="tag", enc="tag", words=rng.sample(TAGS, 4), unit="", desc="current waypoint label (short text)")
        fail = dict(name="fail_cnt", enc="varint", scale=1, lo=0, hi=400, step=3, unit="", desc="count of sensor faults so far",
                    mono=True)
        fb = [_seq(rng), tag, _enum(rng, desc="flight mode", n=5), _plain("rssi_dbm", rng), fail]
        S["types"] = [{"name": "telemetry", "code": codes[0], "fields": fa}, {"name": "status", "code": codes[1], "fields": fb}]
    for t in S["types"]:
        for f in t["fields"]:
            f["wt"] = _weight(f)
            if f["enc"] == "bits":
                for m in f["members"]:
                    m["wt"] = 3
    return S


def _weight(f):
    e = f["enc"]
    if e == "uint":
        return 1 if f.get("seq") or (not f["signed"] and f["scale"] == 1 and (f["w"] == 1 or f["endian"] == "little")) else 2
    return 3


def _listed(t, S):
    lo = S.get("list_order")
    return sorted(t["fields"], key=lambda f: lo.index(f["name"])) if lo else t["fields"]


def _keys(t, S):
    ks = ["kind"] if S["has_type"] else []
    for f in _listed(t, S):
        ks += [m["name"] for m in f["members"]] if f["enc"] == "bits" else [f["name"]]
    return ks


# ---------------------------------------------------------------- encoding
def _crc8(bs, poly):
    c = 0
    for b in bs:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ poly) & 255 if c & 128 else (c << 1) & 255
    return c


def _chk(S, bs):
    k = S["chk"]["kind"]
    if k == "sum8":
        return sum(bs) & 255
    if k == "xor8":
        x = 0
        for b in bs:
            x ^= b
        return x
    return _crc8(bs, S["chk"]["poly"])


def _varint(v):
    out = bytearray()
    while True:
        b = v & 127
        v >>= 7
        out.append(b | (128 if v else 0))
        if not v:
            return bytes(out)


def _init_state(t, rng, bias=None):
    st = {}
    for f in t["fields"]:
        e = f["enc"]
        if e in ("uint", "delta", "varint"):
            if f.get("seq"):
                st[f["name"]] = rng.randint(0, 200)
            elif f.get("tick"):
                st[f["name"]] = 100
            elif f.get("mono"):
                st[f["name"]] = rng.randint(0, 150)
            elif "start_hi" in f:
                st[f["name"]] = rng.randint(0, f["start_hi"])
            elif bias and f.get("signed") and f["lo"] < 0 and f["hi"] > 0 and f["w"] == 2:
                # tier 4 trap: the shown examples never go negative, the hidden dump mostly does
                q = (f["hi"] - f["lo"]) // 4
                st[f["name"]] = rng.randint(f["hi"] // 2, f["hi"] - 3 * f["step"]) if bias == "hi" else rng.randint(f["lo"], f["lo"] + q)
            else:
                q = (f["hi"] - f["lo"]) // 4
                st[f["name"]] = rng.randint(f["lo"] + q, f["hi"] - q)
        elif e == "enum":
            st[f["name"]] = rng.randrange(len(f["names"]))
        elif e == "tag":
            st[f["name"]] = rng.choice(f["words"])
        elif e == "bits":
            for m in f["members"]:
                st[m["name"]] = rng.randrange(1 << m["nbits"])
    return st


def _advance(t, st, rng):
    for f in t["fields"]:
        e, n = f["enc"], f["name"]
        if e in ("uint", "delta", "varint"):
            if f.get("seq"):
                st[n] += 1
            elif f.get("tick"):
                st[n] += rng.randint(95, 105)
            elif f.get("mono"):
                st[n] = min(f["hi"], st[n] + (rng.randint(f["step"] // 2, f["step"]) if f["step"] > 1 else rng.randint(0, 2)))
            else:
                st[n] = max(f["lo"], min(f["hi"], st[n] + rng.randint(-f["step"], f["step"])))
        elif e == "enum":
            if rng.random() < 0.35:
                st[n] = rng.randrange(len(f["names"]))
        elif e == "tag":
            if rng.random() < 0.3:
                st[n] = rng.choice(f["words"])
        elif e == "bits":
            for m in f["members"]:
                if rng.random() < 0.35:
                    st[m["name"]] = rng.randrange(1 << m["nbits"])


def _payload(t, st, prev):
    out = bytearray()
    for f in t["fields"]:
        e, n = f["enc"], f["name"]
        if e == "uint":
            out += st[n].to_bytes(f["w"], f["endian"], signed=f["signed"])
        elif e == "delta":
            out += (st[n] - prev.get(n, 0)).to_bytes(f["w"], f["endian"], signed=f["signed"])
        elif e == "varint":
            out += _varint(st[n])
        elif e == "enum":
            out.append(st[n])
        elif e == "tag":
            out += bytes([len(st[n])]) + st[n].encode()
        elif e == "bits":
            b = 0
            for m in f["members"]:
                b |= st[m["name"]] << m["shift"]
            out.append(b)
    return bytes(out)


def _phys(f, raw):
    sc = f.get("scale", 1)
    return round(raw / sc, 6) if sc != 1 else raw


def _record(S, t, st):
    d = {"kind": t["name"]} if S["has_type"] else {}
    row = [("kind", t["name"], 0, 1)] if S["has_type"] else []
    for f in t["fields"]:
        e, n = f["enc"], f["name"]
        if e == "bits":
            for m in f["members"]:
                v = bool(st[m["name"]]) if m["kind"] == "bool" else st[m["name"]]
                d[m["name"]] = v
                row.append((m["name"], v, 0, m["wt"]))
        else:
            v = f["names"][st[n]] if e == "enum" else st[n] if e == "tag" else _phys(f, st[n])
            d[n] = v
            sc = f.get("scale", 1)
            row.append((n, v, (1.0 / sc if sc != 1 else 0) if e in ("uint", "delta", "varint") else 0, f["wt"]))  # one stored unit, as the prompt promises (was 0.6; scrub pass-1)
    return d, row


def _frame(S, t, payload):
    body = bytearray()
    lm = S["len_mode"]
    if lm:
        n = len(payload) + (1 if "t" in lm else 0) + (1 if "c" in lm else 0)
        body.append(n)
    if S["has_type"]:
        body.append(t["code"])
    body += payload
    span = payload if S["chk"]["span"] == "payload" else bytes(body)
    return bytes(S["sync"]) + bytes(body) + bytes([_chk(S, span)])


def make_dump(S, seed_key, nframes, n_corrupt, head_clean, bias=None):
    rng = random.Random(seed_key)
    types = S["types"]
    slots = []
    for i in range(nframes):
        ti = (i if i < 2 else 0 if rng.random() < 0.65 else 1) if len(types) == 2 else 0
        slots.append(ti)
    bad = set()
    while len(bad) < n_corrupt:
        c = rng.randrange(head_clean, nframes)
        if c in bad or c - 1 in bad or c + 1 in bad:
            continue
        bad.add(c)
        if len(bad) == n_corrupt and not any(b >= nframes - 4 for b in bad):
            bad.clear()
    sync = bytes(S["sync"])
    seq0 = rng.randint(0, 200)
    st = [_init_state(t, rng, bias) for t in types]
    prev = [{} for _ in types]
    first = [True] * len(types)
    data, exp, nbad = bytearray(), [], 0
    for i in range(nframes):
        ti = slots[i]
        t = types[ti]
        while True:
            st_try = copy.deepcopy(st[ti])
            if not first[ti]:
                _advance(t, st_try, rng)
            for f in t["fields"]:
                if f.get("seq"):
                    st_try[f["name"]] = seq0 + i
            payload = _payload(t, st_try, prev[ti])
            fr = _frame(S, t, payload)
            if i in bad:
                b = bytearray(fr)
                lo = 2 + (1 if S["len_mode"] else 0) + (1 if S["has_type"] else 0)
                pos = rng.randrange(lo, len(b)) if len(b) > lo else len(b) - 1
                b[pos] ^= rng.randint(1, 255)
                fr = bytes(b)
            if sync not in fr[1:]:
                break
        first[ti] = False
        # state advances for every frame; the delta reference only moves when the frame is accepted
        st[ti] = st_try
        if i in bad:
            nbad += 1
        else:
            d, row = _record(S, t, st_try)
            exp.append({"rec": d, "row": row, "seq": d.get("seq")})
            prev[ti] = {f["name"]: st_try[f["name"]] for f in t["fields"] if f["enc"] == "delta"}
        if S["tier"] in (2, 3) and rng.random() < 0.15 and i:
            data += bytes(rng.choice([x for x in range(256) if x != sync[0]]) for _ in range(rng.randint(1, 3)))
        data += fr
    return bytes(data), exp, nbad


# ---------------------------------------------------------------- prompt
def _hexdump(b):
    return "\n".join(f"{o:04x}: " + " ".join(f"{x:02x}" for x in b[o:o + 16]) for o in range(0, len(b), 16))


def _rng_txt(f):
    sc = f.get("scale", 1)
    if f["enc"] == "enum":
        return "one of [" + ", ".join(f["names"]) + "] (stored as the 0-based position of the name in this list)"
    if f["enc"] == "tag":
        return "short ASCII text, e.g. " + ", ".join(f["words"][:3])
    if f.get("tick"):
        return "ms, increases steadily"
    if f.get("seq"):
        return "integer, counts up"
    if f.get("mono"):
        return (f"integer {f['unit']}, only ever increases").replace("integer , ", "integer, ")
    lo, hi = f["lo"] / sc, f["hi"] / sc
    if f["enc"] == "delta" and not f.get("tick"):
        lo, hi = 0, 3000
    return f"{lo:g} to {hi:g} {f['unit']}".strip()


def _prompt(S, dump, kdec, nframes):
    lines = []
    for t in S["types"]:
        if S["has_type"]:
            lines.append(f"Record kind `{t['name']}` (output key `kind` = \"{t['name']}\"); fields in the order they appear in the record:")
        else:
            lines.append("Fields of every record (listed in NO particular order; this is not the order of the bytes in the record):"
                         if S.get("list_order") else "Fields of every record, in the order they appear in the record:")
        for f in _listed(t, S):
            if f["enc"] == "bits":
                for m in f["members"]:
                    rr = "true/false" if m["kind"] == "bool" else f"integer 0 to {(1 << m['nbits']) - 1}"
                    lines.append(f"- `{m['name']}`: {m['desc']} ({rr})")
            else:
                lines.append(f"- `{f['name']}`: {f['desc']} ({_rng_txt(f)})")
    names = [n for t in S["types"] for n in _keys(t, S)]
    ex = "\n".join(json.dumps({k: r["rec"][k] for k in names} if S.get("list_order") else r["rec"]) for r in kdec)
    dmg = ("\nSome frames in a dump were damaged in transit. The recorder protects every frame with an integrity check of unknown "
           "type; a correct decoder returns only intact records and silently drops damaged frames. A damaged frame does not "
           "count as the previous record for anything. Stray bytes may also sit between frames.") if S["tier"] in (2, 3) else ""
    return f"""A drone's flight recorder was recovered. Its memory arrived as a raw binary dump in a vendor format nobody documented. The dump is a series of frames, each starting with a fixed marker.{dmg}

What a record means (this is NOT the byte layout):
{chr(10).join(lines)}
Numbers are decoded to their physical meaning (for example a voltage stored in hundredths of a volt is returned in volts, as a float); enums are returned by name; booleans as true/false.

Hex dump of {nframes} frames from one flight:
{_hexdump(dump)}

The decoded values of the first {len(kdec)} intact records of that dump, as one JSON object per line:
{ex}

Write a Python 3 function `decode(data: bytes) -> list[dict]` (standard library only) that decodes a dump of this format from a DIFFERENT flight (different values, same recorder) into the list of intact records, in order, each a dict with exactly the keys named above (string keys, values as described). Floats are accepted within one least-significant stored unit. You are scored per field per record on a hidden dump, plus on getting the record count right{' and on dropping damaged frames' if S['tier'] in (2, 3) else ''}. Your code runs with a {CPU_S} s CPU limit. Reply with the complete code in a single ```python block."""


def gen(seed, tier):
    S = gen_schema(seed, tier)
    nf, nc, k = {1: (14, 0, 7), 2: (18, 2, 4), 3: (24, 4, 2), 4: (3, 0, 3)}[tier]
    hn, hc = {1: (16, 0), 2: (20, 3), 3: (28, 5), 4: (16, 0)}[tier]
    dump, exp, _ = make_dump(S, f"blackbox:pub:{seed}:{tier}", nf, nc, k, "hi" if tier == 4 else None)
    hdump, hexp, hbad = make_dump(S, f"blackbox:hid:{seed}:{tier}", hn, hc, 2, "lo" if tier == 4 else None)
    kdec = exp[:k]
    return {"prompt": _prompt(S, dump, kdec, nf),
            "public": {"hex": hdump.hex(), "names": [n for t in S["types"] for n in _keys(t, S) if n != "kind"][:], "sync": S["sync"],
                       "tier": tier, "phex": dump.hex(), "kdec": [r["rec"] for r in kdec], "kinds": [t["name"] for t in S["types"]]},
            "hidden": {"S": S, "exp": [r["row"] for r in hexp], "seq": [r["seq"] for r in hexp], "nbad": hbad}}


# ---------------------------------------------------------------- grading
def _eq(a, b, tol):
    if isinstance(b, str):
        return isinstance(a, str) and a == b
    if isinstance(b, bool):
        return a in (True, False) and bool(a) == b
    if isinstance(a, (str, list, dict)) or a is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol + 1e-9
    except (TypeError, ValueError):
        return False


def score(inst, out):
    h = inst["hidden"]
    exp, seqs, n = h["exp"], h["seq"], len(h["exp"])
    outs = [o if isinstance(o, dict) else {} for o in out]

    def fields(matched):
        got = tot = 0.0
        for row, o in zip(exp, matched):
            for k, v, tol, w in row:
                tot += w
                if o is not None and k in o and _eq(o[k], v, tol):
                    got += w
        return got / tot

    by_seq = {}
    for o in outs:
        s = o.get("seq")
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            by_seq.setdefault(int(s), o)
    fa = fields([by_seq.get(s) for s in seqs])
    fb = fields([outs[j] if j < len(outs) else None for j in range(n)])
    F = max(fa, 0.5 * fb)
    C = max(0.0, 1 - abs(len(outs) - n) / n)
    es = set(seqs)
    osq = [int(o["seq"]) for o in outs if isinstance(o.get("seq"), (int, float)) and not isinstance(o.get("seq"), bool)]
    hit = len(es & set(osq))
    spur = len(osq) - len([s for s in osq if s in es]) + (len(osq) - len(set(osq))) + (len(outs) - len(osq))
    S_ = max(0.0, (hit - 2 * spur) / n)
    q = 0.1 * C + 0.2 * S_ + 0.7 * F if h["nbad"] else 0.1 * C + 0.9 * F
    return q, {"C": round(C, 3), "S": round(S_, 3), "F": round(F, 3)}


def key_classes(S):
    """output key -> ladder rung: kind | plain | scaled (signed / big-endian / scaled number) | enum | bits | varint | delta | tag"""
    cls = {}
    if S["has_type"]:
        cls["kind"] = "kind"
    for t in S["types"]:
        for f in t["fields"]:
            if f["enc"] == "bits":
                for m in f["members"]:
                    cls.setdefault(m["name"], "bits")
            else:
                cls.setdefault(f["name"], ("plain" if f["wt"] == 1 else "scaled") if f["enc"] == "uint" else f["enc"])
    return cls


def _tally(exp, matched):
    got, tot = {}, {}
    for row, o in zip(exp, matched):
        for k, v, tol, w in row:
            tot[k] = tot.get(k, 0.0) + w
            if o is not None and k in o and _eq(o[k], v, tol):
                got[k] = got.get(k, 0.0) + w
    return got, tot


def score_checks(inst, out):
    """The ladder of ``score`` as named checks. -> (q, results): results = [{id, credit, pass, weight, group, detail}] with
    sum(weight * credit) == q (``score``'s q) and sum(weight) == 1. Checks: count (framing / record count), skip (damaged frames
    dropped, only when the dump has any), then one check per output key named ``<rung>/<key>`` (``kind`` for the record kind), each
    weighted by its share of the field weight (ff weights: plain 1, signed/endian/scale 2, the rest 3)."""
    h = inst["hidden"]
    exp, seqs, n = h["exp"], h["seq"], len(h["exp"])
    outs = [o if isinstance(o, dict) else {} for o in out]
    by_seq = {}
    for o in outs:
        s = o.get("seq")
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            by_seq.setdefault(int(s), o)
    ga, tot = _tally(exp, [by_seq.get(s) for s in seqs])
    gb, _ = _tally(exp, [outs[j] if j < len(outs) else None for j in range(n)])
    T = sum(tot.values())
    fa, fb = sum(ga.values()) / T, sum(gb.values()) / T
    use_seq = fa >= 0.5 * fb
    C = max(0.0, 1 - abs(len(outs) - n) / n)
    es = set(seqs)
    osq = [int(o["seq"]) for o in outs if isinstance(o.get("seq"), (int, float)) and not isinstance(o.get("seq"), bool)]
    hit = len(es & set(osq))
    spur = len(osq) - len([s for s in osq if s in es]) + (len(osq) - len(set(osq))) + (len(outs) - len(osq))
    S_ = max(0.0, (hit - 2 * spur) / n)
    damaged = bool(h["nbad"])
    wf = 0.7 if damaged else 0.9
    cls = key_classes(h["S"])
    res = [{"id": "count", "credit": round(C, 6), "weight": 0.1, "group": "framing",
            "detail": f"{len(outs)} records out, {n} intact in the dump"}]
    if damaged:
        res.append({"id": "skip", "credit": round(S_, 6), "weight": 0.2, "group": "damage",
                    "detail": f"{hit} intact frames kept, {spur} spurious or repeated, {h['nbad']} damaged in the dump"})
    for k in tot:
        c = (ga.get(k, 0.0) if use_seq else 0.5 * gb.get(k, 0.0)) / tot[k]
        r = cls.get(k, "plain")
        res.append({"id": k if r == "kind" else f"{r}/{k}", "credit": round(c, 6), "weight": round(wf * tot[k] / T, 6),
                    "group": r, "detail": "matched by seq" if use_seq else "matched by position (half credit)"})
    for r in res:
        r["pass"] = r["credit"] >= 0.999
    q = sum(r["weight"] * r["credit"] for r in res)
    return q, res


# ---------------------------------------------------------------- anchors
def baseline(inst):
    if inst["public"]["tier"] == 4:
        return _baseline4(inst)
    """lazy: split on the sync bytes, read the listed fields as consecutive aligned little-endian u16"""
    names = inst["public"]["names"]
    return f'''import struct
NAMES = {names!r}
def decode(data):
    out = []
    for part in data.split(data[:2])[1:]:
        r = {{}}
        for i, n in enumerate(NAMES):
            if 2 * i + 2 > len(part):
                break
            r[n] = struct.unpack_from("<H", part, 2 * i)[0]
        out.append(r)
    return out
'''


_B4 = r'''
SYNC = bytes(SYNCL)
def frames(data):
    return data.split(SYNC)[1:]
PF = frames(PH)
PL = len(PF[0]) - 1
ex = [(PF[i][:PL], KD[i]) for i in range(len(KD))]
def rd(p, pos, c):
    w, end, sg, sc = c
    v = int.from_bytes(p[pos:pos + w], end, signed=sg)
    return v / sc if sc != 1 else v
def decode(data):
    lay = {}
    for n in NAMES:
        v0 = KD[0][n]
        if isinstance(v0, (bool, str)):
            continue
        for pos in range(PL):
            hit = None
            for w in (1, 2):
                if pos + w > PL:
                    continue
                for end in ("little", "big"):
                    if w == 1 and end == "big":
                        continue
                    for sg in (False, True):
                        for sc in (1, 10, 16, 100, 1000):
                            if abs(rd(ex[0][0], pos, (w, end, sg, sc)) - v0) <= 0.6 / sc + 1e-9:
                                hit = (pos, (w, end, sg, sc)); break
                        if hit: break
                    if hit: break
                if hit: break
            if hit:
                lay[n] = hit; break
    out = []
    for fr in frames(data):
        r = {}
        for n in NAMES:
            r[n] = rd(fr, *lay[n]) if n in lay else KD[0][n]
        out.append(r)
    return out
'''


def _baseline4(inst):
    """generic anchor for tier 4: infer each field from the FIRST example only (first byte offset / width / endianness / scale that
    reproduces it, unsigned before signed), copy non-numeric fields from the example"""
    p = inst["public"]
    return f"PH = bytes.fromhex({p['phex']!r})\nKD = {p['kdec']!r}\nNAMES = {p['names']!r}\nSYNCL = {p['sync']!r}\n" + _B4


def reference(inst):
    S = inst["hidden"]["S"]
    return "import json\nS = json.loads(%r)\n" % json.dumps(S) + _REF


_REF = r'''
def crc8(bs, poly):
    c = 0
    for b in bs:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ poly) & 255 if c & 128 else (c << 1) & 255
    return c

def chk(bs):
    k = S["chk"]["kind"]
    if k == "sum8":
        return sum(bs) & 255
    if k == "xor8":
        x = 0
        for b in bs:
            x ^= b
        return x
    return crc8(bs, S["chk"]["poly"])

def fixed_len(t):
    return sum(f["w"] if f["enc"] == "uint" else 1 for f in t["fields"])

def parse(t, p, prev):
    d = {"kind": t["name"]} if S["has_type"] else {}
    pos, newprev = 0, {}
    for f in t["fields"]:
        e = f["enc"]
        if e in ("uint", "delta"):
            raw = int.from_bytes(p[pos:pos + f["w"]], f["endian"], signed=f["signed"]); pos += f["w"]
            if e == "delta":
                raw += prev.get(f["name"], 0); newprev[f["name"]] = raw
            d[f["name"]] = raw / f["scale"] if f["scale"] != 1 else raw
        elif e == "varint":
            v = sh = 0
            while True:
                b = p[pos]; pos += 1
                v |= (b & 127) << sh; sh += 7
                if not b & 128:
                    break
            d[f["name"]] = v
        elif e == "enum":
            d[f["name"]] = f["names"][p[pos]]; pos += 1
        elif e == "tag":
            n = p[pos]; d[f["name"]] = p[pos + 1:pos + 1 + n].decode(); pos += 1 + n
        elif e == "bits":
            b = p[pos]; pos += 1
            for m in f["members"]:
                v = (b >> m["shift"]) & ((1 << m["nbits"]) - 1)
                d[m["name"]] = bool(v) if m["kind"] == "bool" else v
    if pos != len(p):
        raise ValueError
    return d, newprev

def decode(data):
    out, prev, i, sync = [], {}, 0, bytes(S["sync"])
    lm = S["len_mode"]
    while True:
        i = data.find(sync, i)
        if i < 0:
            break
        p = i + 2
        try:
            ln = None
            if lm:
                ln = data[p]; p += 1
            t = S["types"][0]
            if S["has_type"]:
                code = data[p]; p += 1
                t = next(x for x in S["types"] if x["code"] == code)
            if lm:
                pl = ln - (1 if "t" in lm else 0) - (1 if "c" in lm else 0)
            else:
                pl = fixed_len(t)
            payload = data[p:p + pl]
            if len(payload) != pl:
                raise ValueError
            span = payload if S["chk"]["span"] == "payload" else data[i + 2:p + pl]
            if chk(span) != data[p + pl]:
                raise ValueError
            d, newprev = parse(t, payload, prev.get(t["name"], {}))
        except (ValueError, IndexError, StopIteration):
            i += 2
            continue
        prev.setdefault(t["name"], {}).update(newprev)
        out.append(d)
        i = p + pl + 1
    return out
'''
