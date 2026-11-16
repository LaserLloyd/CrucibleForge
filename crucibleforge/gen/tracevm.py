"""tracevm (G2): seeded "predict the trace" items (stdlib only).

An instance seed yields a program for a fully specified 4-register mini-VM
(15 ops, halting by construction) and the question "what does it print?".
Grading is exact per output line with prefix credit (an early mistake forfeits
the later lines): the materialised case uses the engine's ``checks`` grader
with one regex per prefix length, and ``grade()`` (the reference grader)
agrees with it.

Level table (30 seeds per level; spec tokens = chars/4; asserted in tests):
  level  instructions  steps executed  output lines  spec tokens
    1      6-10          9               4              ~640
    2      12-18         29-58           6-13           ~665
    3      20-30         41-91           5-20           ~685
    4      35-50         73-132          6-19           ~745
    5      60-80         126-195         7-23           ~820

4.5 quirk levels (run_q; "semantics that cut against habit", S8-DESIGN section 2):
    6      60-80         150-220         10-20          ~1,110   delay slot, saturating sub
    7      70-95         200-300         12-22          ~1,250   + signed jlt, one data-dependent loop
    8      80-110        250-380         14-24          ~1,370   + memory ld/st (m[Y mod 8])
Every quirk is stated with one worked example (tested), and an instance is accepted only when each
quirk-blind VM (that quirk switched off) first differs within the first 40% of the output lines, so
prefix credit punishes ignoring any quirk. Levels 6-8 send max_tokens 6144. The inverse "?" question
(L9) is not built (it waits for P1). Levels 1-5 and run() are unchanged (golden digests in the tests).
"""
import hashlib
import random
import re
import sys

MASK = 0xFFFF
STEP_LIMIT = 2000
DATA = ("r0", "r1")
ALLREG = ("r0", "r1", "r2", "r3")

# (min_instr, max_instr)
SIZE = {1: (6, 10), 2: (12, 18), 3: (20, 30), 4: (35, 50), 5: (60, 80)}


# ---------------------------------------------------------------- VM
def parse(lines):
    prog = []
    for ln in lines:
        t = ln.split()
        if t and t[0].endswith(":"):  # tolerate "12: add r0 r1"
            t = t[1:]
        prog.append(tuple(t))
    return prog


def run(lines, limit=STEP_LIMIT):
    """Reference interpreter. Returns (output_lines, steps, branches_taken, halted_naturally)."""
    prog = parse(lines)
    r = {"r0": 0, "r1": 0, "r2": 0, "r3": 0}
    pc, steps, taken, out = 0, 0, 0, []
    while 0 <= pc < len(prog):
        if steps >= limit:
            return out, steps, taken, False
        steps += 1
        ins = prog[pc]
        op = ins[0]
        pc += 1
        if op == "set":
            r[ins[1]] = int(ins[2]) & MASK
        elif op == "add":
            r[ins[1]] = (r[ins[1]] + r[ins[2]]) & MASK
        elif op == "sub":
            r[ins[1]] = (r[ins[1]] - r[ins[2]]) & MASK
        elif op == "mul":
            r[ins[1]] = (r[ins[1]] * r[ins[2]]) & MASK
        elif op == "xor":
            r[ins[1]] = r[ins[1]] ^ r[ins[2]]
        elif op == "shr":
            r[ins[1]] = r[ins[1]] >> int(ins[2])
        elif op == "mod":
            if r[ins[2]] != 0:
                r[ins[1]] = r[ins[1]] % r[ins[2]]
        elif op == "dec":
            r[ins[1]] = (r[ins[1]] - 1) & MASK
        elif op in ("jz", "jnz"):
            if (r[ins[1]] == 0) == (op == "jz"):
                pc = int(ins[2])
                taken += 1
        elif op == "jlt":
            if r[ins[1]] < r[ins[2]]:
                pc = int(ins[3])
                taken += 1
        elif op == "out":
            out.append(str(r[ins[1]]))
        elif op == "outc":
            c = r[ins[1]] % 128
            out.append(chr(c) if 32 <= c <= 126 else "\\x%02x" % c)
        elif op == "halt":
            return out, steps, taken, True
        else:
            raise ValueError("bad instruction %r" % (ins,))
    return out, steps, taken, True  # ran off the end


# ---------------------------------------------------------------- generator
def _rng(seed, level, attempt):
    s = f"tracevm:{seed}:{level}" + (f":r{attempt}" if attempt else "")
    return random.Random(int.from_bytes(hashlib.sha256(s.encode()).digest(), "big"))


class Asm:
    def __init__(self, rng, level):
        self.r, self.level, self.p, self.nloops = rng, level, [], 0

    def emit(self, t):
        self.p.append(t)
        return len(self.p) - 1

    def alu(self):
        r = self.r
        ops = ["add", "sub", "mul", "xor", "shr", "set", "add", "mul", "xor"]
        if self.level >= 4:
            ops += ["mod", "mod", "xor"]
        op = r.choice(ops)
        d = r.choice(DATA)
        if op == "set":
            self.emit(("set", d, str(r.randint(1, 600))))
        elif op == "shr":
            self.emit(("shr", d, str(r.randint(1, 6))))
        else:
            s = r.choice([x for x in ALLREG if x != d])
            self.emit((op, d, s))

    def out(self):
        op = "outc" if self.level >= 4 and self.r.random() < 0.45 else "out"
        self.emit((op, self.r.choice(DATA)))

    def branch(self, nskip):
        r = self.r
        k = r.choice(["jlt", "jlt", "jlt", "jnz", "jz"])
        idx = self.emit(None)
        for _ in range(nskip):
            self.out() if r.random() < 0.25 else self.alu()
        tgt = len(self.p)
        if k == "jlt":
            a, b = r.sample(DATA, 2)
            self.p[idx] = ("jlt", a, b, str(tgt))
        else:
            self.p[idx] = (k, r.choice(DATA), str(tgt))

    def unit(self, ops, branch, outp):
        for _ in range(ops):
            self.alu()
        if branch:
            self.branch(1 if self.level == 1 else self.r.randint(1, 2))
        if self.r.random() < outp:
            self.out()

    def loop(self, counter, k, body):
        self.nloops += 1
        self.emit(("set", counter, str(k)))
        start = len(self.p)
        body()
        self.emit(("dec", counter))
        self.emit(("jnz", counter, str(start)))

    def prelude(self):
        self.emit(("set", "r0", str(self.r.randint(1, 9999))))
        self.emit(("set", "r1", str(self.r.randint(1, 9999))))


def _build(rng, level):
    a, r = Asm(rng, level), rng
    ri = r.randint
    a.prelude()
    if level == 1:
        a.alu()
        a.branch(1)
        for reg in ("r0", "r1", "r0", "r1"):
            a.emit(("out", reg))
    elif level == 2:
        a.unit(1, False, 1.0)
        a.loop("r3", ri(3, 5), lambda: (a.unit(1, True, 0.7), a.unit(1, True, 0.5)))
        a.out(); a.out()
    elif level == 3:
        a.unit(ri(1, 2), False, 1.0)
        def outer():
            a.unit(ri(1, 2), False, 0.3)
            a.loop("r2", ri(2, 3), lambda: a.unit(ri(1, 2), True, 0.5))
            a.out()
        a.loop("r3", ri(2, 3), outer)
        a.unit(1, True, 0.5)
        a.out()
    elif level == 4:
        a.unit(3, False, 0.0)
        def outer():
            a.unit(3, True, 0.0)
            a.loop("r2", ri(2, 3), lambda: a.unit(3, True, 0.3))
            a.out()
        a.loop("r3", ri(2, 3), outer)
        a.unit(2, True, 0.0)
        a.loop("r2", ri(2, 3), lambda: (a.unit(3, True, 0.0), a.out()))
        a.out(); a.out()
    else:
        a.unit(3, False, 0.0)
        def nested(ko, ki, outp):
            def outer():
                a.unit(2, True, 0.0)
                a.loop("r2", ki, lambda: a.unit(2, True, outp))
                a.out()
            a.loop("r3", ko, outer)
        nested(ri(2, 3), ri(2, 3), 0.15)
        a.unit(3, True, 0.5)
        a.loop("r2", ri(3, 5), lambda: (a.unit(2, True, 0.0), a.unit(1, True, 0.2)))
        a.out()
        nested(2, ri(2, 3), 0.1)
        a.unit(2, True, 0.0)
        a.out(); a.out()
    a.emit(("halt",))
    return [" ".join(t) for t in a.p], a.nloops


def _accept(lines, level):
    lo, hi = SIZE[level]
    if not lo <= len(lines) <= hi:
        return None
    out, steps, taken, ok = run(lines)
    if not ok or not 4 <= len(out) <= 24 or len(set(out)) < (2 if level == 1 else 3):
        return None
    if any(not o.strip() for o in out) or taken < (1 if level == 1 else 3):
        return None
    ops = {l.split()[0] for l in lines}
    if level >= 4 and not {"outc", "mod", "xor"} <= ops:
        return None
    return out, steps, taken


def _spec(program, level):
    if level >= 6:
        return _spec_q(program, level)
    numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(program))
    return f"""You are given a program for a tiny virtual machine (VM). Work out exactly what it prints.

VM DEFINITION
- Four registers: r0, r1, r2, r3. All start at 0. Every register always holds an integer in 0..65535: after every arithmetic instruction (set, add, sub, mul, dec) the result is reduced modulo 65536, so values wrap around (for example 0 - 1 gives 65535, and 65535 + 1 gives 0).
- The program is a list of instructions, one per line. Lines are numbered from 0 (the number before the colon is the line number, it is not part of the instruction). A program counter (pc) starts at line 0. Unless an instruction jumps, pc moves to the next line after it executes.
- Jump targets are absolute line numbers. If the pc becomes a line number that does not exist (past the last line), the program halts.
- Every executed instruction, including halt and jumps that are not taken, counts as one step. If the program would execute more than {STEP_LIMIT} instructions, it stops immediately and the answer is exactly what was printed so far.

INSTRUCTIONS (X and Y are registers, N is a literal integer, L is a line number)
- set X N     : X = N
- add X Y     : X = (X + Y) mod 65536
- sub X Y     : X = (X - Y) mod 65536
- mul X Y     : X = (X * Y) mod 65536
- xor X Y     : X = X bitwise-XOR Y
- shr X N     : X = X shifted right by N bits (logical shift, zeros come in on the left; same as integer division by 2^N, rounding down)
- mod X Y     : X = X mod Y (remainder of non-negative integer division); if Y is 0, X is left unchanged
- dec X       : X = (X - 1) mod 65536 (so 0 becomes 65535)
- jz X L      : if X == 0, pc = L, otherwise continue with the next line
- jnz X L     : if X != 0, pc = L, otherwise continue with the next line
- jlt X Y L   : if X < Y (both read as non-negative integers), pc = L, otherwise continue with the next line
- out X       : print the decimal value of X on its own line (no leading zeros, no padding)
- outc X      : let c = X mod 128. If c is between 32 and 126 inclusive, print the single character with ASCII code c on its own line. Otherwise print the four-character text \\x followed by c as two lowercase hex digits (for example c = 10 prints \\x0a, c = 7 prints \\x07).
- halt        : stop the program

PROGRAM
{numbered}

TASK
Run the program from line 0 and report everything it prints, in order.
Reply with the program's complete output, one line per printed item, inside a single ```text fenced block and nothing else.
"""


def generate(seed: int, level: int) -> dict:
    if level not in SIZE:
        raise ValueError("level must be 1..5 or 6..8")
    if level >= 6:
        return _generate_q(seed, level)
    for attempt in range(100000):
        rng = _rng(seed, level, attempt)
        lines, nloops = _build(rng, level)
        res = _accept(lines, level)
        if res:
            out, steps, taken = res
            return {
                "spec": _spec(lines, level),
                "program": "\n".join(f"{i}: {l}" for i, l in enumerate(lines)),
                "expected": list(out),
                "checkpoints": list(out),
                "meta": {"level": level, "n_instructions": len(lines), "steps_executed": steps,
                         "n_branches_taken": taken, "n_loops": nloops, "attempt": attempt},
            }
    raise RuntimeError("no acceptable program found")


# ---------------------------------------------------------------- quirk VM (levels 6+)
# 4.5: "semantics that cut against habit". The default VM above is unchanged for levels 1-5; levels 6+
# run on run_q with a set of QUIRKS switched on:
#   delay    a taken jump executes the next line (its delay slot) before control moves
#   sat      sub saturates at 0 (dec still wraps)
#   signed   jlt reads 32768..65535 as negative
#   memwrap  (L8) ld/st address m[Y mod 8]; the blind VM uses the raw address
QUIRK_LEVELS = {6: ("delay", "sat"), 7: ("delay", "sat", "signed"), 8: ("delay", "sat", "signed", "memwrap"),
                9: ("delay", "sat", "signed", "memwrap")}
QSIZE = {6: dict(instr=(60, 80), steps=(150, 220), out=(10, 20)),
         7: dict(instr=(70, 95), steps=(200, 300), out=(12, 22)),
         8: dict(instr=(80, 110), steps=(250, 380), out=(14, 24)),
         9: dict(instr=(105, 150), steps=(380, 600), out=(16, 24))}      # round-2 knob: three data-dependent loops
SIZE.update({lv: d["instr"] for lv, d in QSIZE.items()})
BLIND_FRACTION = 0.4        # every quirk-blind run must differ within the first 40% of the output lines


def run_q(lines, quirks, limit=STEP_LIMIT):
    """Interpreter for the quirk VM. ``quirks`` = the quirks that are ON. Returns like run()."""
    prog = parse(lines)
    r = {"r0": 0, "r1": 0, "r2": 0, "r3": 0}
    mem: dict = {}
    out: list = []
    st = {"steps": 0, "taken": 0}

    def sgn(v):
        return v - 65536 if "signed" in quirks and v >= 32768 else v

    def addr(v):
        return v % 8 if "memwrap" in quirks else v

    def ex(ins):
        """execute one instruction; returns ('jump', target), ('halt',) or None"""
        op = ins[0]
        if op == "set":
            r[ins[1]] = int(ins[2]) & MASK
        elif op == "add":
            r[ins[1]] = (r[ins[1]] + r[ins[2]]) & MASK
        elif op == "sub":
            d = r[ins[1]] - r[ins[2]]
            r[ins[1]] = max(d, 0) if "sat" in quirks else d & MASK
        elif op == "mul":
            r[ins[1]] = (r[ins[1]] * r[ins[2]]) & MASK
        elif op == "xor":
            r[ins[1]] = r[ins[1]] ^ r[ins[2]]
        elif op == "shr":
            r[ins[1]] = r[ins[1]] >> int(ins[2])
        elif op == "mod":
            if r[ins[2]] != 0:
                r[ins[1]] = r[ins[1]] % r[ins[2]]
        elif op == "dec":
            r[ins[1]] = (r[ins[1]] - 1) & MASK
        elif op == "ld":
            r[ins[1]] = mem.get(addr(r[ins[2]]), 0)
        elif op == "st":
            mem[addr(r[ins[2]])] = r[ins[1]]
        elif op in ("jz", "jnz"):
            if (r[ins[1]] == 0) == (op == "jz"):
                return ("jump", int(ins[2]))
        elif op == "jlt":
            if sgn(r[ins[1]]) < sgn(r[ins[2]]):
                return ("jump", int(ins[3]))
        elif op == "out":
            out.append(str(r[ins[1]]))
        elif op == "outc":
            c = r[ins[1]] % 128
            out.append(chr(c) if 32 <= c <= 126 else "\\x%02x" % c)
        elif op == "halt":
            return ("halt",)
        else:
            raise ValueError("bad instruction %r" % (ins,))
        return None

    pc = 0
    while 0 <= pc < len(prog):
        if st["steps"] >= limit:
            return out, st["steps"], st["taken"], False
        st["steps"] += 1
        res = ex(prog[pc])
        pc += 1
        if res is None:
            continue
        if res[0] == "halt":
            return out, st["steps"], st["taken"], True
        st["taken"] += 1
        if "delay" in quirks and pc < len(prog):
            if st["steps"] >= limit:
                return out, st["steps"], st["taken"], False
            st["steps"] += 1
            slot = ex(prog[pc])
            if slot is not None and slot[0] == "halt":
                return out, st["steps"], st["taken"], True
        pc = res[1]
    return out, st["steps"], st["taken"], True


def first_divergence(truth, other):
    """index of the first output line where ``other`` differs from ``truth`` (a shorter or longer output
    diverges where the shorter one ends); None when identical"""
    for i, (a, b) in enumerate(zip(truth, other)):
        if a != b:
            return i
    return None if len(truth) == len(other) else min(len(truth), len(other))


class AsmQ(Asm):
    """Assembler for the quirk VM: every jump is followed by a delay-slot line that is never a jump."""

    def alu(self):
        r = self.r
        ops = ["add", "sub", "sub", "mul", "xor", "shr", "set", "add", "mul", "xor", "mod", "mod", "sub"]
        if self.level >= 8:
            ops += ["st", "ld", "st", "ld"]
        op = r.choice(ops)
        d = r.choice(DATA)
        if op == "set":
            self.emit(("set", d, str(r.randint(1, 600))))
        elif op == "shr":
            self.emit(("shr", d, str(r.randint(1, 6))))
        else:
            s = r.choice([x for x in ALLREG if x != d])
            self.emit((op, d, s))

    def slot(self):
        (self.out if self.r.random() < 0.08 else self.alu)()

    def branch(self, nskip):
        r = self.r
        k = r.choice(["jlt", "jlt", "jlt", "jnz", "jz"])
        idx = self.emit(None)
        self.slot()                               # the delay slot runs whether or not the jump is taken
        for _ in range(max(1, nskip - 1)):
            self.out() if r.random() < 0.1 else self.alu()
        tgt = len(self.p)
        if k == "jlt":
            a, b = r.sample(DATA, 2)
            self.p[idx] = ("jlt", a, b, str(tgt))
        else:
            self.p[idx] = (k, r.choice(DATA), str(tgt))

    def unit(self, ops, branch, outp):
        for _ in range(ops):
            self.alu()
        if branch:
            self.branch(self.r.randint(2, 3))
        if self.r.random() < outp:
            self.out()

    def loop(self, counter, k, body):
        self.nloops += 1
        self.emit(("set", counter, str(k)))
        start = len(self.p)
        body()
        self.emit(("dec", counter))
        self.emit(("jnz", counter, str(start)))
        self.slot()

    def dloop(self, body, src):
        """do-while loop whose trip count comes from the data: r3 = src mod M, r2 doubles from s until it
        reaches r3 (jlt r2 r3). Body must not touch r2/r3 and must not hold loops."""
        r = self.r
        self.nloops += 1
        for t in (("set", "r3", "0"), ("add", "r3", src), ("set", "r2", str(r.randrange(300, 701))),
                  ("mod", "r3", "r2"), ("set", "r2", str(r.randint(1, 5)))):
            self.emit(t)
        start = len(self.p)
        body()
        self.emit(("add", "r2", "r2"))
        self.emit(("jlt", "r2", "r3", str(start)))
        self.slot()


def _build_q(rng, level):
    a, r = AsmQ(rng, level), rng
    ri = r.randint
    a.prelude()
    a.unit(2, True, 1.0)

    def nested(ko, ki, outp):
        def outer():
            a.unit(1, True, 0.0)
            a.loop("r2", ki, lambda: a.unit(1, True, outp))
            a.out()
        a.loop("r3", ko, outer)
    nested(ri(2, 3), ri(2, 3), 0.1)
    a.unit(2, True, 0.6)
    if level >= 7:
        a.dloop(lambda: a.unit(2, True, 0.12), r.choice(DATA))
        a.out()
    a.loop("r2", ri(3, 4) if level >= 8 else ri(2, 3), lambda: (a.unit(2, True, 0.0), a.unit(1, True, 0.1)))
    a.out()
    if level >= 8:
        a.loop("r3", ri(3, 4), lambda: a.unit(2, True, 0.0))
        a.out()
    nested(2, ri(2, 3), 0.1)
    if level >= 9:
        a.dloop(lambda: (a.unit(2, True, 0.05), a.unit(2, True, 0.05)), r.choice(DATA))
        a.out()
        a.dloop(lambda: a.unit(3, True, 0.05), r.choice(DATA))
        a.out()
    a.unit(2, True, 0.5)
    a.out()
    a.emit(("halt",))
    return [" ".join(t) for t in a.p], a.nloops


def _accept_q(lines, level):
    d = QSIZE[level]
    if not d["instr"][0] <= len(lines) <= d["instr"][1]:
        return None
    quirks = set(QUIRK_LEVELS[level])
    out, steps, taken, ok = run_q(lines, quirks)
    if not ok or not d["out"][0] <= len(out) <= d["out"][1] or not d["steps"][0] <= steps <= d["steps"][1]:
        return None
    if len(set(out)) < 3 or any(not o.strip() for o in out) or taken < 3:
        return None
    ops = {l.split()[0] for l in lines}
    need = {"outc", "mod", "xor", "sub"} | ({"ld", "st"} if level >= 8 else set())
    if not need <= ops:
        return None
    blind = {}
    for q in QUIRK_LEVELS[level]:
        bo = run_q(lines, quirks - {q})[0]
        div = first_divergence(out, bo)
        if div is None or div >= BLIND_FRACTION * len(out):
            return None
        blind[q] = bo
    return out, steps, taken, blind


# (program lines, expected output) for each quirk; the spec prints them and a test re-runs them on run_q
QUIRK_EXAMPLES = {
    "delay": (["set r0 1", "jnz r0 4", "set r1 7", "set r1 9", "out r1"], ["7"]),
    "sat": (["set r0 3", "set r1 5", "sub r0 r1", "out r0", "dec r0", "out r0"], ["0", "65535"]),
    "signed": (["set r0 40000", "set r1 5", "jlt r0 r1 5", "set r2 1", "set r2 2", "out r2"], ["1"]),
    "memwrap": (["set r0 42", "set r1 11", "st r0 r1", "set r2 3", "ld r3 r2", "out r3"], ["42"]),
}


def _ex_text(q):
    prog, out = QUIRK_EXAMPLES[q]
    body = "\n".join(f"    {i}: {l}" for i, l in enumerate(prog))
    return f"  Example:\n{body}\n  prints: {', '.join(out)}"


def _spec_q(program, level):
    quirks = QUIRK_LEVELS[level]
    numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(program))
    rules = [
        ("DELAY SLOT", "When a jump is TAKEN on line i, the instruction on line i+1 (the delay slot) is still executed "
         "once, and only after it does the pc become the jump target. A jump that is not taken has no special effect: "
         "the pc moves to line i+1 and that line runs as usual. The slot counts as a step and its printing, if any, "
         "happens. No jump is ever on a line right after a jump, and no jump is the last line.", "delay"),
        ("SATURATING SUB", "sub X Y gives 0 when Y is greater than X (it does not wrap). dec still wraps "
         "(0 becomes 65535).", "sat"),
    ]
    if "signed" in quirks:
        rules.append(("SIGNED COMPARE", "jlt reads a register value of 32768 or more as negative: the value v is "
                      "read as v - 65536 in the comparison (only in jlt; the register itself is unchanged and "
                      "every other instruction sees 0..65535).", "signed"))
    if "memwrap" in quirks:
        rules.append(("MEMORY", "There are 8 memory cells m0..m7, all 0 at the start. ld X Y sets X = m[Y mod 8]. "
                      "st X Y sets m[Y mod 8] = X (the value of X is stored; X is unchanged).", "memwrap"))
    rt = "\n".join(f"{i}. {name}. {txt}\n{_ex_text(q)}" for i, (name, txt, q) in enumerate(rules, 1))
    mem = ("- ld X Y      : X = m[Y mod 8] (see MEMORY)\n- st X Y      : m[Y mod 8] = X (see MEMORY)\n"
           if "memwrap" in quirks else "")
    return f"""You are given a program for a tiny virtual machine (VM). Work out exactly what it prints.
This VM does not behave like the usual textbook machine: the rules marked below differ on purpose. Follow them exactly.

VM DEFINITION
- Four registers: r0, r1, r2, r3. All start at 0. Every register always holds an integer in 0..65535: after set, add, mul and dec the result is reduced modulo 65536, so values wrap around (for example 65535 + 1 gives 0). sub is different (see SATURATING SUB).
- The program is a list of instructions, one per line. Lines are numbered from 0 (the number before the colon is the line number, it is not part of the instruction). A program counter (pc) starts at line 0. Unless an instruction jumps, pc moves to the next line after it executes.
- Jump targets are absolute line numbers. If the pc becomes a line number that does not exist (past the last line), the program halts.
- Every executed instruction, including halt, jumps that are not taken and delay-slot instructions, counts as one step. If the program would execute more than {STEP_LIMIT} instructions, it stops immediately and the answer is exactly what was printed so far.

THE RULES THAT DIFFER FROM THE USUAL
{rt}

INSTRUCTIONS (X and Y are registers, N is a literal integer, L is a line number)
- set X N     : X = N
- add X Y     : X = (X + Y) mod 65536
- sub X Y     : X = X - Y, but 0 if Y > X (see SATURATING SUB)
- mul X Y     : X = (X * Y) mod 65536
- xor X Y     : X = X bitwise-XOR Y
- shr X N     : X = X shifted right by N bits (logical shift; same as integer division by 2^N, rounding down)
- mod X Y     : X = X mod Y (remainder of non-negative integer division); if Y is 0, X is left unchanged
- dec X       : X = (X - 1) mod 65536 (so 0 becomes 65535)
{mem}- jz X L      : if X == 0, jump to line L (with a delay slot, see DELAY SLOT), otherwise continue with the next line
- jnz X L     : if X != 0, jump to line L (with a delay slot), otherwise continue with the next line
- jlt X Y L   : if X < Y, jump to line L (with a delay slot), otherwise continue with the next line{"; the comparison is signed, see SIGNED COMPARE" if "signed" in quirks else " (both read as non-negative integers)"}
- out X       : print the decimal value of X on its own line (no leading zeros, no padding)
- outc X      : let c = X mod 128. If c is between 32 and 126 inclusive, print the single character with ASCII code c on its own line. Otherwise print the four-character text \\x followed by c as two lowercase hex digits (for example c = 10 prints \\x0a, c = 7 prints \\x07).
- halt        : stop the program

PROGRAM
{numbered}

TASK
Run the program from line 0 and report everything it prints, in order.
Reply with the program's complete output, one line per printed item, inside a single ```text fenced block and nothing else.
"""


def _generate_q(seed: int, level: int) -> dict:
    for attempt in range(200000):
        rng = _rng(seed, level, attempt)
        lines, nloops = _build_q(rng, level)
        res = _accept_q(lines, level)
        if res:
            out, steps, taken, blind = res
            return {
                "spec": _spec(lines, level),
                "program": "\n".join(f"{i}: {l}" for i, l in enumerate(lines)),
                "expected": list(out),
                "checkpoints": list(out),
                "blind": blind,
                "meta": {"level": level, "n_instructions": len(lines), "steps_executed": steps,
                         "n_branches_taken": taken, "n_loops": nloops, "attempt": attempt,
                         "quirks": list(QUIRK_LEVELS[level])},
            }
    raise RuntimeError("no acceptable quirk program found")


# ---------------------------------------------------------------- grader
def extract_answer(text: str) -> list:
    blocks, cur = [], None
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            if cur is None:
                cur = []
            else:
                blocks.append(cur)
                cur = None
        elif cur is not None:
            cur.append(ln)
    if cur is not None and not blocks:
        blocks.append(cur)  # unclosed fence, nothing better
    if blocks:
        return [l.rstrip("\r") for l in blocks[-1]]
    return [l.rstrip("\r") for l in text.splitlines() if l.strip()]


def grade_lines(answer_text: str, expected: list) -> dict:
    got = [l.strip() for l in extract_answer(answer_text)]
    exp = [e.strip() for e in expected]
    m = 0
    while m < len(exp) and m < len(got) and got[m] == exp[m]:
        m += 1
    pool = list(got)
    loose = 0
    for e in exp:
        if e in pool:
            pool.remove(e)
            loose += 1
    total = len(exp)
    return {"score": m / total if total else 0.0, "matched": m, "total": total,
            "detail": f"prefix-match {m}/{total}; set-match {loose}/{total}; answer lines {len(got)}"}


def _mutant_mid(seed: int, level: int) -> str:
    exp = list(generate(seed, level)["expected"])
    i = len(exp) // 2
    e = exp[i]
    exp[i] = str((int(e) + 1) % 65536) if e.isdigit() else ("?" if e != "?" else "!")
    return "```text\n" + "\n".join(exp) + "\n```"


# ------------------------------------------------------------ generator interface
from .. import gen as _gen  # noqa: E402  (one seed derivation: gen.instance_seed)

FAMILY = "tracevm"
LEVELS = {lv: {"instructions": SIZE[lv]} for lv in SIZE}

# median reference-answer tokens (chars/4, 30 seeds; tests/test_gen_answer_tokens.py re-measures)
_ANSWER_TOKENS = {1: 7, 2: 13, 3: 10, 4: 13, 5: 18, 6: 15, 7: 17, 8: 20, 9: 20}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]



def _instance(gen: dict, release_seed: str, case_id: str) -> tuple[dict, int, int]:
    level = int(gen["level"])
    if level not in SIZE:
        raise ValueError(f"tracevm: unknown level {level}")
    seed = _gen.instance_int(FAMILY, release_seed, case_id, level, gen.get("key"))
    return generate(seed, level), seed, level


def _prefix_pattern(lines: list[str]) -> str:
    body = r"[ \t]*\r?\n[ \t]*".join(re.escape(x.strip()) for x in lines)
    return r"```(?:text)?[ \t]*\r?\n[ \t]*" + body + r"[ \t]*(?:\r?\n|```|\Z)"


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    g, seed, level = _instance(gen, release_seed, case_id)
    exp = g["expected"]
    return {"prompt": g["spec"], "temperature": 0.0, "max_tokens": 6144 if level >= 6 else 2048,
            "grader": "lines_prefix", "grader_config": {"expected": list(exp)},
            "expected": list(exp), "meta": g["meta"]}


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    g, _, _ = _instance(gen, release_seed, case_id)
    return "```text\n" + "\n".join(g["expected"]) + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    g, _, _ = _instance(gen, release_seed, case_id)
    exp = g["expected"]

    def bump(e: str) -> str:
        return str((int(e) + 1) % 65536) if e.isdigit() else ("?" if e != "?" else "!")

    def fence(lines):
        return "```text\n" + "\n".join(lines) + "\n```"
    mid, first = list(exp), list(exp)
    mid[len(exp) // 2] = bump(exp[len(exp) // 2])
    first[0] = bump(exp[0])
    out = [fence(mid), fence(first), fence(exp[:-1]), fence(exp[::-1] if exp[::-1] != exp else exp[1:])]
    out += [fence(b) for b in (g.get("blind") or {}).values()]       # one quirk-blind output per quirk
    return out


def grade(answer: str, materialized: dict) -> float:
    return grade_lines(answer, materialized["expected"])["score"]



if __name__ == "__main__":
    _m = materialize({"type": FAMILY, "level": int(sys.argv[2]) if len(sys.argv) > 2 else 2},
                     release_seed=sys.argv[1] if len(sys.argv) > 1 else "dev", case_id="TV-demo")
    print(_m["prompt"])
    print("# expected:", _m["expected"], "meta:", _m["meta"])
