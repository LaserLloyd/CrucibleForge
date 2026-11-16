"""Helper of gen/blackbox.py: the red-team floors of the fast-fail question BLACK BOX (t-work/blackbox/test_blackbox.py for tiers 1-3,
redteam4.py for tier 4), as source strings the engine grader runs: nothing, copy the shown records, the lazy aligned-u16 reading
(``baseline``), a greedy first-example fit, and (tier 4) a brute-force layout search that sees only the public examples.
Pinned by ``_FLOORS_SHA`` in blackbox.py."""
import json
from . import _blackbox_ff as _ff

NOTHING = "def decode(data):\n    return []\n"


def copy_code13(inst):
    return f"EX={inst['public']['kdec']!r}\ndef decode(data):\n    n=len(data)//12\n    return [dict(EX[i%len(EX)]) for i in range(n)]\n"

def greedy_code13(inst):
    """guess widths/endianness/scale by matching the FIRST example only, greedy in listed order; no checksum, no delta/varint"""
    return f'''
import struct
KD={inst['public']['kdec']!r}
NAMES={inst['public']['names']!r}
SYNC=bytes({inst['public']['sync']!r})
def cand(raw, want):
    # raw: bytes at pos; try widths
    for w in (1,2,3,4):
        for end in ("little","big"):
            for sg in (False, True):
                if len(raw)<w: continue
                v=int.from_bytes(raw[:w],end,signed=sg)
                for sc in (1,10,16,100,1000):
                    if abs(v/sc-want)<=0.6/sc+1e-9: return (w,end,sg,sc)
    return None
def layout(part, ex):
    best=None
    for off in range(0,5):
        pos=off; lay=[]; hit=0
        for n in NAMES:
            if n not in ex: continue
            w=ex[n]
            if isinstance(w,(int,float)) and not isinstance(w,bool):
                c=cand(part[pos:],w)
                if c: lay.append((n,pos,c)); pos+=c[0]; hit+=1; continue
            lay.append((n,pos,None)); pos+=1
        if best is None or hit>best[0]: best=(hit,lay)
    return best[1]
def decode(data):
    parts=data.split(SYNC)[1:]
    if not parts: return []
    lay=layout(parts[0],KD[0])
    out=[]
    for p in parts:
        r={{}}
        for n,pos,c in lay:
            if c is None: r[n]=0; continue
            w,end,sg,sc=c
            if pos+w<=len(p):
                v=int.from_bytes(p[pos:pos+w],end,signed=sg); r[n]=v/sc if sc!=1 else v
        out.append(r)
    return out
'''



COMMON4 = '''
import itertools
PH = bytes.fromhex(%(phex)r)
KD = %(kdec)r
SYNC = bytes(%(sync)r)
NAMES = %(names)r
ENUMS = %(enums)r
def frames(data):
    return [p for p in data.split(SYNC)[1:]]
PF = frames(PH)
FL = len(PF[0])   # payload + checksum
PL = FL - 1
ex = [(PF[i][:PL], KD[i]) for i in range(len(KD))]
def numcands(pos, want_fn):
    """all (w, endian, signed, scale) such that every example matches at pos"""
    out = []
    for w in (1, 2):
        if pos + w > PL: continue
        for end in ("little", "big"):
            if w == 1 and end == "big": continue
            for sg in (False, True):
                for sc in (1, 10, 16, 100, 1000):
                    out.append((w, end, sg, sc))
    return out
def rd(p, pos, c):
    w, end, sg, sc = c
    v = int.from_bytes(p[pos:pos + w], end, signed=sg)
    return v / sc if sc != 1 else v
def okc(name, pos, c):
    for p, d in ex:
        if abs(rd(p, pos, c) - d[name]) > 0.6 / c[3] + 1e-9: return False
    return True
'''

def _public_src4(inst):
    pub = inst["public"]
    S = inst["hidden"]["S"]
    enums = {f["name"]: f["names"] for f in S["types"][0]["fields"] if f["enc"] == "enum"}   # enum lists are in the prompt
    return COMMON4 % dict(phex=pub["phex"], kdec=pub["kdec"], sync=pub["sync"], names=pub["names"], enums=enums)

def copy_code4(inst):
    return _public_src4(inst) + '''
def decode(data):
    n = len(frames(data))
    return [dict(KD[i %% len(KD)]) for i in range(n)]
''' .replace("%%", "%")

GREEDY4 = '''
def decode(data):
    lay = {}
    for n in NAMES:
        v0 = KD[0][n]
        if isinstance(v0, bool):
            lay[n] = ("b",); continue
        if isinstance(v0, str):
            lay[n] = ("e",); continue
        found = None
        for pos in range(PL):
            for c in numcands(pos, None):
                if okc(n, pos, c):
                    found = (pos, c); break
            if found: break
        lay[n] = ("n",) + found if found else None
    out = []
    for fr in frames(data):
        r = {}
        for n in NAMES:
            L = lay[n]
            if L and L[0] == "n": r[n] = rd(fr, L[1], L[2])
            else: r[n] = KD[0][n]
        out.append(r)
    return out
'''
def greedy_code4(inst):
    return _public_src4(inst) + GREEDY4

BRUTE4 = '''
def sol():
    """DFS over (field, encoding) per byte position, consistent with ALL examples"""
    scal = [n for n in NAMES if not isinstance(KD[0][n], (bool, str)) and n not in ENUMS and n not in BITN]
    enm = [n for n in NAMES if n in ENUMS]
    bit = [n for n in NAMES if n in BITN]
    def rec(pos, left, bits_done, lay):
        if pos == PL:
            return lay if not left and bits_done else None
        for n in list(left):
            if n in enm:
                if all(ENUMS[n].index(d[n]) == p[pos] for p, d in ex):
                    r = rec(pos + 1, [x for x in left if x != n], bits_done, lay + [(pos, n, "e")])
                    if r: return r
            else:
                for c in numcands(pos, None):
                    if pos + c[0] <= PL and okc(n, pos, c):
                        r = rec(pos + c[0], [x for x in left if x != n], bits_done, lay + [(pos, n, c)])
                        if r: return r
        if bit and not bits_done:
            assigns = []
            for m in bit:
                nb = BITN[m]
                ok = [s for s in range(0, 9 - nb) if all(((p[pos] >> s) & ((1 << nb) - 1)) == int(d[m]) for p, d in ex)]
                assigns.append(ok)
            for combo in itertools.product(*assigns):
                used = 0; good = True
                for m, s in zip(bit, combo):
                    mask = ((1 << BITN[m]) - 1) << s
                    if used & mask: good = False; break
                    used |= mask
                if good:
                    r = rec(pos + 1, left, True, lay + [(pos, "_bits", list(zip(bit, combo)))])
                    if r: return r
        return None
    left = scal + enm
    return rec(0, left, not bit, [])
def decode(data):
    lay = sol()
    out = []
    if not lay: return out
    for fr in frames(data):
        r = {}
        for pos, n, c in lay:
            if c == "e": r[n] = ENUMS[n][fr[pos]]
            elif n == "_bits":
                for m, s in c:
                    v = (fr[pos] >> s) & ((1 << BITN[m]) - 1)
                    r[m] = bool(v) if BOOLS[m] else v
            else: r[n] = rd(fr, pos, c)
        out.append(r)
    return out
'''
def brute_code4(inst):
    S = inst["hidden"]["S"]
    bitn, bools = {}, {}
    for f in S["types"][0]["fields"]:
        if f["enc"] == "bits":
            for m in f["members"]:
                bitn[m["name"]] = m["nbits"]; bools[m["name"]] = m["kind"] == "bool"
    return _public_src4(inst) + f"BITN = {bitn!r}\nBOOLS = {bools!r}\n" + BRUTE4



def lazy_code(inst):
    """the tier-1 lazy baseline (split on sync, consecutive aligned little-endian u16) applied to any tier"""
    i2 = dict(inst)
    i2["public"] = dict(inst["public"], tier=1)
    return _ff.baseline(i2)


def floors(inst):
    """name -> source of every red-team floor that applies to this instance's tier"""
    t = inst["public"]["tier"]
    if t == 4:
        return {"nothing": NOTHING, "copy": copy_code4(inst), "u16lazy": lazy_code(inst), "baseline": _ff.baseline(inst),
                "greedy": greedy_code4(inst), "brute": brute_code4(inst)}
    return {"nothing": NOTHING, "copy": copy_code13(inst), "baseline": _ff.baseline(inst), "greedy": greedy_code13(inst)}
