"""GEN2-P contract tests: patchfix / patchmod (one-shot fix and one-shot modify families).

1. determinism; 2. an independent second implementation written from SPEC.md agrees with the clean
program (so the SPEC, not the code, fixes every graded answer line); 3. every catalogue bug changes the
behaviour SPEC.md fixes; 4. the hidden groups bite correctly (reference passes, untouched base fails
each f2p, passes each p2p; fixing one bug passes exactly its group); 5. scores of reference, partial
answers, SEARCH/REPLACE answers and mutants; 6. prompt hygiene and sizes; 7. one sandboxed
``codebank.verify_case`` per family.
"""
from __future__ import annotations

import difflib
import json
import re
import random

import pytest

from crucibleforge.gen import _patchbase as pb, patchfix, patchmod

SEED = "4.5.0"


def _fix(level, key="p1"):
    return patchfix.materialize({"type": "patchfix", "level": level, "key": key}, release_seed=SEED,
                                case_id=f"T-fix-{level}-{key}")


def _mod(level, key="p1"):
    return patchmod.materialize({"type": "patchmod", "level": level, "key": key}, release_seed=SEED,
                                case_id=f"T-mod-{level}-{key}")


# ----------------------------------------------------------------------- 1. determinism

@pytest.mark.parametrize("fam,lv", [(patchfix, 2), (patchmod, 3)])
def test_deterministic(fam, lv):
    g = {"type": fam.FAMILY, "level": lv, "key": "d1"}
    a = fam.materialize(g, release_seed=SEED, case_id="D")
    fam._CACHE.clear()
    b = fam.materialize(g, release_seed=SEED, case_id="D")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = fam.materialize(g, release_seed="other", case_id="D")
    assert c["prompt"] != a["prompt"]


# ----------------------------------------------------------------------- 2. independent oracle from SPEC.md

class Oracle:
    """Written from SPEC.md only (canonical names). Shares no code with the program."""

    def __init__(self, k, features=()):
        self.k, self.f = k, set(features)
        self.book = {}        # id -> dict
        self.rows = {}        # id -> dict (stored copy incl. wrote)
        self.quota = {}
        self.seq = 0
        self.ev = []
        self.clock = None
        self.waiting = {}     # W id -> request (requests never change, so the store copy is the same object)
        self.wn = 0

    # --- helpers
    def fam(self, h):
        return h.split(self.k["FAMILY_SEP"])[0]

    def dead(self, L, t):
        idle = t - L["last"]
        return idle >= L["ttl"] if self.k["EXPIRE_AT_TTL"] else idle > L["ttl"]

    def beats(self, a, b):
        return (a < b) if self.k["BEST_CLASS"] == 1 else (a > b)

    def log(self, t, kind, text):
        self.ev.append(f"{t} {kind} {text}")
        self.ev = self.ev[-self.k["LOG_KEEP"]:]

    def write(self, L, t):
        self.rows[L["id"]] = dict(L, wrote=t)

    def remove(self, L, t, kind):
        del self.book[L["id"]]
        self.rows.pop(L["id"], None)
        self.log(t, kind, L["id"])

    def soft_write(self, L, t):
        r = self.rows.get(L["id"])
        if r is None or t - r["wrote"] >= self.k["TOUCH_COALESCE"]:
            self.write(L, t)
            return True
        return False

    def by_age(self):
        return sorted(self.book.values(), key=lambda L: L["n"])

    # --- parsing
    def num(self, s):
        if not s.isdigit():
            raise ValueError("number")
        return int(s)

    def units(self, s):
        got = set()
        for part in s.split(","):
            if part == "":
                raise ValueError("units")
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = self.num(a), self.num(b)
                if a > b:
                    raise ValueError("units")
                got |= set(range(a, b + 1))
            else:
                got.add(self.num(part))
        return sorted(got)

    def line(self, ln):
        w = ln.split()
        argc = {"ACQ": 4, "REL": 1, "TOUCH": 1, "SWEEP": 0, "RESTART": 0, "STATUS": 0, "LOG": 1}
        if "quota" in self.f:
            argc["QUOTA"] = 2
        if "extend" in self.f:
            argc["EXTEND"] = 2
        try:
            if len(w) < 2 or w[0][:1] != "@":
                raise ValueError("syntax")
            t = self.num(w[0][1:])
            c = w[1].upper()
            if c not in argc:
                raise ValueError("command")
            a = w[2:]
            if c == "ACQ" and "queue" in self.f and len(w[2:]) == 5 and w[6] == "q":
                a = w[2:6]
            if len(a) != argc[c]:
                raise ValueError("arity")
            if c == "ACQ":
                cl = self.num(a[1])
                if not 1 <= cl <= 3:
                    raise ValueError("class")
                a = [a[0], cl, self.units(a[2]), self.num(a[3]), len(w) == 7]
            elif c == "LOG":
                a = [self.num(a[0])]
            elif c == "QUOTA":
                a = [a[0], self.num(a[1])]
            elif c == "EXTEND":
                n = self.num(a[1])
                if n < 1:
                    raise ValueError("number")
                a = [a[0], n]
        except ValueError as e:
            return [f"ERR parse {e}"]
        if self.clock is not None and t < self.clock:
            return [f"ERR time {t}<{self.clock}"]
        self.clock = t
        pre = self.serve_queue(t) if "queue" in self.f else []
        if c == "REL" and "queue" in self.f and a[0].startswith("W"):
            return pre + self.cancel(t, a[0])
        return pre + getattr(self, "do_" + c)(t, *a)

    # --- queue (section 11)
    def eff(self, r, t):
        steps = (t - r["at"]) // pb.aging_of(self.k)
        c = r["cls"]
        best = self.k["BEST_CLASS"]
        for _ in range(steps):
            if c == best:
                break
            c += -1 if best == 1 else 1
        return c

    def prio(self, t):
        rank = (lambda c: c) if self.k["BEST_CLASS"] == 1 else (lambda c: -c)
        return sorted(self.waiting.values(), key=lambda r: (rank(self.eff(r, t)), r["n"]))

    def serve_queue(self, t):
        out = []
        for r in sorted(self.waiting.values(), key=lambda r: r["n"]):
            waited = t - r["at"]
            if (waited >= r["ttl"]) if self.k["EXPIRE_AT_TTL"] else (waited > r["ttl"]):
                del self.waiting[r["id"]]
                self.log(t, "unqueue", r["id"])
                out.append(f"UNQUEUED {r['id']}")
        held = {u for L in self.book.values() if not self.dead(L, t) for u in L["units"]}
        skipped = set()
        for r in self.prio(t):
            us = set(r["units"])
            if us & held or us & skipped:
                skipped |= us
                continue
            for L in self.by_age():
                if set(L["units"]) & us:
                    self.remove(L, t, "expired")
            self.seq += 1
            L = {"id": f"{self.k['ID_PREFIX']}{self.seq}", "n": self.seq, "holder": r["holder"], "units": r["units"],
                 "cls": r["cls"], "ttl": r["ttl"], "created": t, "last": t, "vac": None}
            self.book[L["id"]] = L
            self.write(L, t)
            del self.waiting[r["id"]]
            self.log(t, "grant", f"{r['id']} {L['id']}")
            held |= us
            out.append(f"GRANT {r['id']} {L['id']}")
        return out

    def cancel(self, t, wid):
        if wid not in self.waiting:
            return [f"ERR unknown {wid}"]
        del self.waiting[wid]
        self.log(t, "cancel", wid)
        return [f"OK {wid} cancelled"]

    def enqueue(self, answer, t, h, cl, us, ttl):
        self.wn += 1
        wid = f"W{self.wn}"
        self.waiting[wid] = {"id": wid, "n": self.wn, "holder": h, "units": us, "cls": cl, "ttl": ttl, "at": t}
        self.log(t, "queue", f"{wid} {h} {','.join(map(str, us))}")
        return [f"{answer} queued={wid}"]

    # --- commands
    def do_ACQ(self, t, h, cl, us, ttl, q=False):
        k = self.k
        bad = [u for u in us if u >= k["UNITS"]]
        if bad:
            return [f"ERR badunit {bad[0]}"]
        if not 1 <= ttl <= k["MAX_TTL"]:
            return [f"ERR badttl {ttl}"]
        for L in self.by_age():
            if L["holder"] == h and L["units"] == us and not self.dead(L, t):
                L["last"] = t
                self.soft_write(L, t)
                return [f"OK {L['id']} again"]
        if "quota" in self.f and self.fam(h) in self.quota:
            fam = self.fam(h)
            used = sum(len(L["units"]) for L in self.book.values()
                       if self.fam(L["holder"]) == fam and not self.dead(L, t))
            if used + len(us) > self.quota[fam]:
                return [f"ERR quota {fam} {used}/{self.quota[fam]}"]
        left = []
        for L in self.by_age():
            if set(L["units"]) & set(us):
                if self.dead(L, t):
                    self.remove(L, t, "expired")
                else:
                    left.append(L)
        if not left:
            self.seq += 1
            L = {"id": f"{k['ID_PREFIX']}{self.seq}", "n": self.seq, "holder": h, "units": us, "cls": cl,
                 "ttl": ttl, "created": t, "last": t, "vac": None}
            self.book[L["id"]] = L
            self.write(L, t)
            self.log(t, "acquire", f"{L['id']} {h} {','.join(map(str, us))}")
            return [f"OK {L['id']}"]
        ids = " ".join(L["id"] for L in left)
        if all(self.beats(cl, L["cls"]) for L in left):
            for L in left:
                if L["vac"] is None:
                    L["vac"] = t + k["VACATE_TIMEOUT"]
                    self.write(L, t)
                    self.log(t, "vacate", L["id"])
            if all(L["vac"] >= t for L in left):
                ans = f"WAIT {ids} retry={k['RETRY_AFTER']}"
                return self.enqueue(ans, t, h, cl, us, ttl) if q else [ans]
        ans = f"ERR conflict {ids}"
        return self.enqueue(ans, t, h, cl, us, ttl) if q else [ans]

    def do_REL(self, t, i):
        if i not in self.book:
            return [f"ERR unknown {i}"]
        self.remove(self.book[i], t, "release")
        return [f"OK {i} released"]

    def do_TOUCH(self, t, i):
        if i not in self.book:
            return [f"ERR unknown {i}"]
        L = self.book[i]
        if self.dead(L, t):
            self.remove(L, t, "expired")
            return [f"ERR expired {i}"]
        L["last"] = t
        return [f"OK {i}" + ("" if self.soft_write(L, t) else " cached")]

    def do_EXTEND(self, t, i, n):
        if i not in self.book:
            return [f"ERR unknown {i}"]
        L = self.book[i]
        if self.dead(L, t):
            self.remove(L, t, "expired")
            return [f"ERR expired {i}"]
        if L["vac"] is not None:
            return [f"ERR vacating {i}"]
        if L["ttl"] + n > self.k["MAX_TTL"]:
            return [f"ERR badttl {L['ttl'] + n}"]
        L["ttl"] += n
        L["last"] = t
        self.write(L, t)
        self.log(t, "extend", f"{i} {L['ttl']}")
        return [f"OK {i} ttl={L['ttl']}"]

    def do_QUOTA(self, t, fam, n):
        if n == 0:
            self.quota.pop(fam, None)
        else:
            self.quota[fam] = n
        self.log(t, "quota", f"{fam} {n}")
        return [f"OK quota {fam}={n}"]

    def do_SWEEP(self, t):
        gone = [L for L in self.by_age() if self.dead(L, t)]
        for L in gone:
            self.remove(L, t, "expired")
        return ["SWEPT " + (" ".join(L["id"] for L in gone) or "-")]

    def do_RESTART(self, t):
        self.book = {}
        kept = dropped = 0
        for r in sorted(self.rows.values(), key=lambda r: r["n"]):
            L = {x: (list(v) if isinstance(v, list) else v) for x, v in r.items() if x != "wrote"}
            if self.fam(L["holder"]) == self.k["OWN_FAMILY"] or self.dead(L, t):
                del self.rows[L["id"]]
                dropped += 1
                continue
            if L["vac"] is not None:
                L["vac"] = None
                self.write(L, t)
            self.book[L["id"]] = L
            kept += 1
        self.log(t, "restart", f"kept={kept} dropped={dropped}")
        return [f"RESTARTED kept={kept} dropped={dropped}"]

    def do_STATUS(self, t):
        k = self.k
        alive = [L for L in self.book.values() if not self.dead(L, t)]
        held = {u for L in alive for u in L["units"]}
        free = ",".join(str(u) for u in range(k["UNITS"]) if u not in held) or "-"
        out = [f"STATUS t={t} leases={len(self.book)} free={free}"]
        rank = (lambda c: c) if k["BEST_CLASS"] == 1 else (lambda c: -c)
        for L in sorted(self.book.values(), key=lambda L: (rank(L["cls"]), L["created"], L["n"])):
            idle = t - L["last"]
            left = L["ttl"] - idle + (0 if k["EXPIRE_AT_TTL"] else 1)
            st = ("vacating" if L["vac"] is not None else "expired" if self.dead(L, t)
                  else "expiring" if left <= k["EXPIRING_WINDOW"] else "active")
            out.append(f"  {L['id']} {L['holder']} u={','.join(map(str, L['units']))} c={L['cls']} {st} idle={idle}")
        if "queue" in self.f:
            for r in self.prio(t):
                out.append(f"  queue {r['id']} {r['holder']} u={','.join(map(str, r['units']))} "
                           f"c={r['cls']}/{self.eff(r, t)} waited={t - r['at']}")
        if "quota" in self.f:
            for fam in sorted(self.quota):
                used = sum(len(L["units"]) for L in alive if self.fam(L["holder"]) == fam)
                out.append(f"  quota {fam} {used}/{self.quota[fam]}")
        return out

    def do_LOG(self, t, n):
        return [f"LOG {n}"] + ["  " + e for e in (self.ev[-n:] if n > 0 else [])]


def oracle_run(k, features, script):
    o = Oracle(k, features)
    out = []
    for ln in script.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out += o.line(ln)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("features", [(), ("quota",), ("extend",), ("quota", "extend"), ("queue",),
                                      ("quota", "extend", "queue")])
def test_oracle_agrees_with_clean_program(features):
    for s in range(12):
        rng = random.Random(f"orc-{s}-{features}")
        k = pb.sample_knobs(rng)
        run = pb.load_runner(pb.build_tree(k, features))
        for _ in range(60):
            lines = pb.gen_script(rng, k, features)
            script = "\n".join(lines) + "\n"
            assert run(script) == oracle_run(k, features, script), (k, script)


# ----------------------------------------------------------------------- 3. the bug catalogue

def test_every_bug_applies_once_and_changes_behaviour():
    feats = ("quota", "extend", "queue")
    seen = {b["name"]: 0 for b in pb.BUGS}
    for s in range(12):
        rng = random.Random(f"cat-{s}")
        k = pb.sample_knobs(rng)
        clean = pb.load_runner(pb.build_tree(k, feats))
        ident = pb.sample_rename(None, identity=True)
        scripts = [pb.to_rendered(pb.gen_script(rng, k, feats), ident) for _ in range(250)]
        want = [clean(x) for x in scripts]
        for b in pb.BUGS:
            if not b.get("when", lambda kk: True)(k):
                continue
            run = pb.load_runner(pb.build_tree(k, feats, (b["name"],)))
            seen[b["name"]] += sum(run(x) != w for x, w in zip(scripts, want))
    dead = [n for n, c in seen.items() if c == 0]
    assert not dead, f"bugs never triggered: {dead}"


def test_bug_spec_sections_exist():
    spec = pb.canonical_src()["SPEC"] + "".join(pb.FEATURE_SPEC.values())
    for b in pb.BUGS:
        sec = b["spec"].split(".")[0]
        assert f"## {sec}." in spec, b["name"]


# ----------------------------------------------------------------------- 4. groups bite correctly (in-process)

def _cases(case, gid):
    src = case["grader_config"]["hidden"][next(g["file"] for g in case["grader_config"]["groups"] if g["id"] == gid)]
    blob = src.split("CASES = json.loads(", 1)[1].split(")\n", 1)[0]
    return json.loads(eval(blob))[gid]          # noqa: S307 - our own generated literal


@pytest.mark.parametrize("lv", [1, 2, 3, 4, 5, 6, 7])
def test_fix_groups_isolate_each_bug(lv):
    c = _fix(lv)
    m = c["meta"]
    from crucibleforge.gen._patchbase import build_tree, load_runner
    rn = {**m["rename"], "noun": tuple(m["rename"]["noun"]), "unit": tuple(m["rename"]["unit"]),
          "book": tuple(m["rename"]["book"])}
    feats = tuple(m["features"])
    base = load_runner(c["grader_config"]["files"], rn)
    ref = load_runner({**c["grader_config"]["files"], **c["reference_files"]}, rn)
    for g in c["grader_config"]["groups"]:
        for script, want in _cases(c, g["id"]):
            assert ref(script) == want
            if g["kind"] == "p2p":
                assert base(script) == want
        if g["kind"] == "f2p":
            assert any(base(s) != w for s, w in _cases(c, g["id"]))
    # fixing exactly one bug passes exactly that bug's group (among the f2p groups)
    for gid, bug in m["group_bug"].items():
        if bug is None:
            continue
        one = load_runner(build_tree(m["knobs"], feats, tuple(b for b in m["bugs"] if b != bug), rn), rn)
        for g in c["grader_config"]["groups"]:
            if g["kind"] != "f2p":
                continue
            ok = all(one(s) == w for s, w in _cases(c, g["id"]))
            assert ok == (g["id"] == gid), (bug, g["id"])


@pytest.mark.parametrize("lv", [1, 2, 3, 4, 5])
def test_mod_groups(lv):
    c = _mod(lv)
    m = c["meta"]
    rn = {**m["rename"], "noun": tuple(m["rename"]["noun"]), "unit": tuple(m["rename"]["unit"]),
          "book": tuple(m["rename"]["book"])}
    base = pb.load_runner(c["grader_config"]["files"], rn)
    ref = pb.load_runner({**c["grader_config"]["files"], **c["reference_files"]}, rn)
    for g in c["grader_config"]["groups"]:
        cases = _cases(c, g["id"])
        assert len(cases) >= 4
        for s, w in cases:
            assert ref(s) == w
            if g["kind"] == "p2p":
                assert base(s) == w
            else:
                assert base(s) != w


# ----------------------------------------------------------------------- 5. scores (sandboxed grader)

def _sr_answer(old: dict, new: dict) -> str:
    """SEARCH/REPLACE blocks: one per group of nearby changes (3 lines of context; SEARCH unique)."""
    out = []
    for p in sorted(new):
        a, b = old[p].splitlines(keepends=True), new[p].splitlines(keepends=True)
        for grp in difflib.SequenceMatcher(None, a, b).get_grouped_opcodes(3):
            i1, i2, j1, j2 = grp[0][1], grp[-1][2], grp[0][3], grp[-1][4]
            search, replace = "".join(a[i1:i2]), "".join(b[j1:j2])
            assert old[p].count(search) == 1
            out.append(f"{p}\n```python\n<<<<<<< SEARCH\n{search}=======\n{replace}>>>>>>> REPLACE\n```\n")
    return "\n".join(out)


def test_fix_scores():
    c = _fix(2)
    assert patchfix.grade(patchfix._files_answer(c["reference_files"]), c) == 1.0
    assert patchfix.grade(_sr_answer(c["grader_config"]["files"], c["reference_files"]), c) == 1.0
    assert patchfix.grade("I could not find the bugs.", c) == 0.0
    K, R = len(c["meta"]["bugs"]), len(c["meta"]["reported"])
    got = patchfix.grade(patchfix._files_answer(c["mutants"]["reported-only"]), c)
    assert got == pytest.approx(R / K)
    # fixing everything but breaking one working rule halves the score
    files = dict(c["reference_files"])
    pkg = c["meta"]["rename"]["package"]
    rd = f"{pkg}/render.py"
    src = files.get(rd, c["grader_config"]["files"][rd])
    assert "free=%s" in src
    files[rd] = src.replace("free=%s", "free:%s")
    assert patchfix.grade(patchfix._files_answer(files), c) <= 0.5


def test_mod_scores():
    c = _mod(5)
    assert patchmod.grade(patchmod._files_answer(c["reference_files"]), c) == 1.0
    assert patchmod.grade(_sr_answer(c["grader_config"]["files"], c["reference_files"]), c) == 1.0
    assert patchmod.grade("", c) == 0.0
    for name, files in c["mutants"].items():
        s = patchmod.grade(patchmod._files_answer(files), c)
        assert 0.0 < s < 1.0, (name, s)


# ----------------------------------------------------------------------- 6. prompt hygiene and sizes

@pytest.mark.parametrize("lv", [1, 2, 3, 4, 5, 6, 7])
def test_fix_prompt(lv):
    c = _fix(lv)
    p, m = c["prompt"], c["meta"]
    for path, text in c["grader_config"]["files"].items():
        if text.strip():
            assert text in p, path
    for b in pb.BUGS:
        assert b["name"] not in p
    assert "bug-1" not in p and "keep-" not in p
    if patchfix.LEVELS[lv].get("count", True):
        assert f"exactly {len(m['bugs'])} bugs" in p
    else:
        assert "how many is not known" in p and str(len(m["bugs"])) + " bugs" not in p
    assert p.count("### Report ") == patchfix.LEVELS[lv]["reported"]
    noun = m["rename"]["noun"][0]
    if noun != "Lease":
        import re
        assert not re.search(r"(?<![A-Za-z])[Ll]ease", p)
    assert 4500 <= m["prompt_tokens"] <= 9000, m["prompt_tokens"]
    assert len(json.dumps(c["grader_config"]["hidden"])) < 400_000
    # the expected output is shown only at the full-report levels
    assert ("should give" in p) == (patchfix.LEVELS[lv]["style"] == "full")


@pytest.mark.parametrize("lv", [1, 2, 3, 4, 5])
def test_mod_prompt(lv):
    c = _mod(lv)
    p = c["prompt"]
    for path, text in c["grader_config"]["files"].items():
        if text.strip():
            assert text in p, path
    for f in patchmod.LEVELS[lv]["add"]:
        sec = {"quota": "## 9.", "extend": "## 10.", "queue": "## 11."}[f]
        assert sec in p
        assert sec not in c["grader_config"]["files"]["SPEC.md"]
    assert 4500 <= c["meta"]["prompt_tokens"] <= 9000


# ----------------------------------------------------------------------- 7. sandboxed verify_case

@pytest.mark.parametrize("make", [lambda: _fix(3), lambda: _mod(2)])
def test_codebank_verify_case(make):
    from crucibleforge import codebank
    c = make()
    case = {**c, "id": "X", "category": "coding"}
    assert codebank.verify_case(case) == []


def test_fix_l7_puts_bugs_in_the_queue():
    from crucibleforge.gen._patchbase import BUG_BY_NAME
    m = _fix(7)["meta"]
    assert "queue" in m["features"]
    assert sum(BUG_BY_NAME[b]["area"] == "queue" for b in m["bugs"]) >= 3


@pytest.mark.parametrize("fam,lv", [(patchfix, lv) for lv in patchfix.LEVELS] + [(patchmod, lv) for lv in patchmod.LEVELS])
def test_no_unfilled_placeholder_in_prompt(fam, lv):
    # round 2 shipped `AGING` (= @AGING@) in the patchmod request: every model failed the same groups
    p = (_fix(lv) if fam is patchfix else _mod(lv))["prompt"]
    assert not re.search(r"@[A-Z_]+@", p)
