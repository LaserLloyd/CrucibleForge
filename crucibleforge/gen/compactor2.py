"""C1 v2 WASTE-COMPACTOR (prototype; the owner's ruling 2026-11-12: turns, old interface, layout, many devices).

An invented deep-storage facility ("<Name> Undercroft") is run from an invented 12-bit minicomputer
monitor, the Orrin FM-7: octal addresses, 4-digit octal words, terse mnemonics and a manual with gaps.
A maintenance crew is trapped in one of twelve refuse presses. The model must decode the monitor,
work out the deck / section / room layout from memory tables and tape files (incomplete, partly
stale), combine that with the crew's radio messages, and halt the right press.

Clock = turns (one command per reply). The crew's press starts at START_TURN and closes at
CLOSE_TURN. Any start/halt/toggle on a device that is not the crew's press is a strike; more than
MAX_STRIKES strikes fails the run. Read-only commands cost a turn but no strike.

Points ladder (0-100):
  floor (0-15)  manual read 5, map key read 5, device table reached 5
  climb (0-25)  MARK with the right deck 12, right deck+section 13 more; auto 25 on a correct halt
  edge  (0-60)  halted the crew's press: 25 + 35*clamp((90-stop_turn)/60) - 4 per strike
A run that strikes out or is crushed keeps floor and MARK climb; edge = 0.
Everything is deterministic from the seed.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field

START_TURN = 32
CLOSE_TURN = 120
MAX_TURNS = 130
MAX_STRIKES = 10
DUMP_WORDS = 32
N_DEV, N_ROOMS, N_PRESS = 300, 150, 30
REF_TURNS = 30       # efficiency: full marks for a stop at this turn ...
EFF_ZERO = 90        # ... none at this turn
PAGE_LINES = 24

DEV_BASE, ROOM_BASE, SENS_BASE = 0o400, 0o3000, 0o4200
DECK_WORDS = ["Lantern", "Brack", "Tollhouse", "Cistern", "Mire", "Keelson", "Ashlar", "Sounding"]
SITE_WORDS = ["Hallowmere", "Quensley", "Dravenholt", "Urskine", "Peltmarsh", "Corrow"]
CREW = ["Ondrel Vask", "Mabry Tull", "Senna Okoro", "Piet Larrow", "Idris Fane", "Wren Halloway"]
FINISHES = ["white enamel", "dark grey", "rust red", "green tile", "bare concrete", "black pitch",
            "pale blue"]
KINDS = ["door", "vent", "light", "pump", "lock", "fan", "heater"]


def o(n, w=4):
    return format(n, "o").zfill(w)


@dataclass
class Room:
    deck: int
    section: int
    room: int
    finish: int     # finish code 1..7
    sensor: int     # sensor id 0..63
    wet: int        # moisture reading

    @property
    def loc(self):
        return self.deck * 0o100 + self.section * 0o10 + self.room


@dataclass
class Device:
    code: int       # octal device code
    kind: str
    room: Room
    running: bool
    role: str = ""  # target | decoy tag for presses
    pos: int = 0
    inhibited: bool = False
    start: int = 0   # a press that is idle until this turn (0 = running from the outset)


@dataclass
class Instance:
    seed: str
    site: str
    decks: dict
    holding: int
    target: Device
    devices: list
    rooms: list
    type_codes: dict
    finish_code: dict        # finish name -> code
    halt_fn: int
    files: dict
    hints: dict              # turn -> message
    grates: list
    ref_turns: int = 0
    notes: dict = field(default_factory=dict)


def generate(seed: str) -> Instance:
    rng = random.Random(f"compactor3:{seed}")
    site = rng.choice(SITE_WORDS)
    names = rng.sample(DECK_WORDS, 6)
    decks = {i + 1: names[i] for i in range(6)}
    H = rng.randint(2, 4)                 # holding-cell deck
    T = H + 1                             # crew's deck: directly below
    C = rng.choice([d for d in range(2, 7) if d not in (H, T) and d - 1 not in (H, T)])
    D2 = C - 1                            # a lock cage (not the cells) above the decoy press deck
    O = rng.choice([d for d in range(1, 7) if d not in (H, T, C)])
    codes = rng.sample(range(1, 8), 7)
    finish_code = dict(zip(FINISHES, codes))
    grey = finish_code["dark grey"]
    others = [c for c in codes if c != grey]
    sT, sD, sE, sX = rng.sample(range(1, 8), 4)   # target, sealed-grate, dry-grey, no-grate sections
    sensors = rng.sample(range(0o400), 0o400)
    rooms = {}

    def room(d, s, r, fin, wet):
        k = (d, s, r)
        if k not in rooms:
            rooms[k] = Room(d, s, r, fin, sensors[len(rooms)], wet)
        return rooms[k]

    wet = lambda: rng.randint(0o400, 0o700)   # noqa: E731
    dry = lambda: rng.randint(0o5, 0o60)      # noqa: E731
    rT = rng.randint(1, 7)
    rN = rT % 7 + 1
    tgt_room = room(T, sT, rT, grey, wet())
    nd_room = room(T, sT, rN, finish_code["rust red"], wet())             # next door: wet, rust, running
    sealed_room = room(T, sD, rng.randint(1, 7), grey, wet())             # grey wet, grate sealed
    dryg_room = room(T, sE, rng.randint(1, 7), grey, dry())               # grey dry, open grate, idle
    nogr_room = room(T, sX, rng.randint(1, 7), grey, wet())               # grey wet, no grate from above
    cage_room = room(C, rng.randint(1, 7), rng.randint(1, 7), grey, wet())  # grey wet below the lock cage
    oth_room = room(O, rng.randint(1, 7), rng.randint(1, 7), grey, wet())
    cage_rooms, hold_rooms = [], []
    while len(cage_rooms) < 4:
        r_ = room(D2, rng.randint(1, 7), rng.randint(1, 7), rng.choice(others), dry())
        if all(r_ is not x for x in cage_rooms):
            cage_rooms.append(r_)
    while len(hold_rooms) < 12:
        r_ = room(H, rng.randint(1, 7), rng.randint(1, 7), rng.choice(others), dry())
        if all(r_ is not x for x in hold_rooms):
            hold_rooms.append(r_)
    while len(rooms) < N_ROOMS:
        d, s, r = rng.randint(1, 6), rng.randint(1, 7), rng.randint(1, 7)
        if d == H:
            continue
        pool = others + ([grey] * 2 if d != T else [])
        room(d, s, r, rng.choice(pool), wet() if rng.random() < 0.15 else dry())
    room_list = list(rooms.values())
    rng.shuffle(room_list)                                                 # unsorted table
    named = {id(r) for r in (tgt_room, nd_room, sealed_room, dryg_room, nogr_room, cage_room, oth_room)}

    dev_codes = rng.sample(range(0o10, 0o1000), N_DEV)
    devices, ci = [], 0

    def add(kind, rm, running, role="", start=0):
        nonlocal ci
        dv = Device(dev_codes[ci], kind, rm, running, role,
                    rng.randint(0, 0o77) if running and not start else 0, start=start)
        ci += 1
        devices.append(dv)
        return dv

    target = add("press", tgt_room, False, "target")
    add("press", nd_room, True, "next-door", START_TURN)
    add("press", sealed_room, True, "sealed-grate", START_TURN)
    add("press", dryg_room, True, "dry-grey", START_TURN)
    add("press", nogr_room, True, "no-grate", START_TURN)
    add("press", cage_room, True, "below-cage", 34)
    add("press", oth_room, True, "grey-wet-other-deck", 30)
    cand = [r for r in room_list if r.deck not in (H, T) and id(r) not in named]
    while len([d for d in devices if d.kind == "press"]) < N_PRESS:
        u = rng.random()
        add("press", rng.choice(cand), u < 0.7, "other", rng.randint(20, 70) if 0.45 <= u < 0.7 else 0)
    for _ in range(45):
        add("lock", rng.choice(hold_rooms), rng.random() < 0.5)
    for _ in range(9):
        add("lock", rng.choice(cage_rooms), rng.random() < 0.5)
    for _ in range(3):
        add("lock", rng.choice(room_list), rng.random() < 0.5)
    plain = [k for k in KINDS if k != "lock"]
    while len(devices) < N_DEV:
        add(rng.choice(plain), rng.choice(room_list), rng.random() < 0.55)
    rng.shuffle(devices)

    tcodes = dict(zip(["press"] + KINDS, rng.sample(range(0o10, 0o60), 8)))
    halt_fn = rng.choice([4, 5])
    start_fn = 2 if halt_fn != 2 else 3

    # grates: three from the cells to deck T (open / sealed / open), one open cage grate, noise
    def gid():
        return f"GR-{o(rng.randint(1, 0o77), 2)}"
    grates = [(gid(), H, rng.randint(1, 7), T, sT, "open"),
              (gid(), H, rng.randint(1, 7), T, sD, "sealed"),
              (gid(), H, rng.randint(1, 7), T, sE, "open"),
              (gid(), D2, rng.randint(1, 7), C, cage_room.section, "open")]
    for _ in range(22):
        a = rng.choice([d for d in range(1, 6) if d not in (H, D2)])
        grates.append((gid(), a, rng.randint(1, 7), a + 1, rng.randint(1, 7), "open"))
    rng.shuffle(grates)
    sealed = [g for g in grates if g[5] == "sealed"][0]

    crew = rng.sample(CREW, 2)
    stencil = sorted([sT, sE])
    hints = {
        6: f"RADIO ({crew[0]}): Crew of four, stuck in a refuse chamber. Hatch dogged shut from outside.",
        12: f"RADIO ({crew[0]}): We came down through a floor grate from the level with the cells. Cells, not refuse.",
        18: f"RADIO ({crew[1]}): Walls are a dark grey. Water on the floor, past our ankles.",
        25: f"RADIO ({crew[0]}): Pitch dark apart from one red lamp. Floor is slick.",
        START_TURN: f"RADIO ({crew[0]}): The walls just started moving!",
        44: f"RADIO ({crew[1]}): There's a stencil by the hatch: S-{stencil[0]} or S-{stencil[1]}, paint's half gone.",
        60: f"RADIO ({crew[0]}): We can hear another press grinding through the wall. Shut that one off?",
        80: f"RADIO ({crew[1]}): Walls are about halfway. Hurry.",
        100: f"RADIO ({crew[0]}): Can barely stand up in here.",
        112: f"RADIO ({crew[1]}): Almost no room left.",
    }

    inst = Instance(seed, site, decks, H, target, devices, room_list, tcodes, finish_code, halt_fn, {},
                    hints, grates, ref_turns=REF_TURNS,
                    notes={"start_fn": start_fn, "sealed": sealed[0], "T": T, "sT": sT, "C": C, "D2": D2,
                           "O": O, "sE": sE})
    inst.files = _files(inst, rng)
    return inst


def _files(inst: Instance, rng) -> dict:
    tc = inst.type_codes
    F = {}
    F["MANUAL"] = f"""ORRIN FM-7 FACILITY MONITOR - OPERATOR CARD (REV C)
Words are 12 bits, written as 4 octal digits. Addresses are octal.
COMMANDS (one per line, upper case)
  HE            this card (short form)
  LS            list tape library
  TY NAME [P]   type tape file NAME, page P (default 1)
  EX AAAA       examine one word
  DP AAAA       dump {DUMP_WORDS} words from AAAA
  IOT DDDD F    device operation, device code DDDD (octal), function F
  MARK DSR      log your fix on a location word (octal) for the shift record
  WT            wait one cycle
IOT FUNCTIONS: 1 read status (safe). {inst.notes['start_fn']} start. Higher numbers: [card torn]
  Other functions are reserved. A wrong-device operation is logged as a FAULT.
LOW CORE
  0100 device table address    0101 device count
  0102 room table address      0103 room count
  0104 [smudged]               0105 cycle counter
DEVICE TABLE ENTRY (4 words): code, location word, type/flags, status
  type/flags: high 2 digits type, low 2 digits flags
  status: high 2 digits position or level, low digit bit 1 powered, bit 2 running, bit 4 inhibited
TYPE CODES
  {o(tc['door'], 2)} door      {o(tc['vent'], 2)} vent      {o(tc['light'], 2)} light
  {o(tc['heater'], 2)} heater    {o(tc['lock'], 2)} ....      [remaining entries water-damaged]
ROOM TABLE ENTRY (4 words): location word, finish code, sensor id, spare
  (table is in installation order)
SENSOR BLOCK: one word per sensor id, moisture reading (0000 dry .. 0777 flooded).
  Block address: see 2000 in older cards.
"""
    F["MAPKEY"] = "LOCATION WORD KEY (maint. office copy)\n  location word = D S R in octal: deck, section, room\n" \
        "DECKS\n" + "".join(
            f"  {d}  {inst.decks[d]}{'  (cells, pre-refit)' if d == inst.notes['D2'] else ''}\n"
            for d in range(1, 7) if d != inst.holding + 2) + \
        f"  {inst.holding + 2}  (see refit notice)\n" \
        "Deck numbers count downward from the surface.\n"
    F["GRATES"] = "FLOOR GRATES AND CHUTES (from deck/section -> to deck/section)\n" + "".join(
        f"  {g[0]}  {g[1]}/{g[2]} -> {g[3]}/{g[4]}\n" for g in inst.grates)
    fin = list(inst.finish_code.items())
    rng.shuffle(fin)
    F["FINISH"] = "WALL FINISH CODES\n" + "".join(
        f"  {c}  {'[illegible]' if n in ('green tile', 'black pitch') else n}\n" for n, c in fin)
    presses = [d for d in inst.devices if d.kind == "press" and d.role == "other"]
    pr = presses[0]
    pump = [d for d in inst.devices if d.kind == "pump"][0]
    tagnum = lambda d: f"RP-{rng.randint(1, 99):02d}"   # noqa: E731
    ptag = f"RP-{rng.randint(1, 99):02d}"
    tags = {}
    # TAGS: ~60 entries, press entries partly wrong
    pool = rng.sample(inst.devices, 58)
    lines = []
    for d in pool:
        if d.kind == "press":
            t = rng.choice(["RP-??", "PRS", "RP-?", "--"])
        else:
            t = rng.choice(["RP-?", "PRS", "V-ST", "LMP 3", "--", "DR", "XX", "P-3"])
        lines.append((d.code, t))
    lines.append((pr.code, ptag))
    rng.shuffle(lines)
    F["TAGS"] = "DEVICE TAGS (partial, field survey; RP = refuse press)\n" + "".join(
        f"  {o(c)}  {t}\n" for c, t in lines)
    hold_name = inst.decks[inst.holding]
    noise_dev = rng.sample([d for d in inst.devices if d.kind not in ("press", "lock")], 30)
    log = []
    for i, d in enumerate(noise_dev):
        fn = rng.choice([1, 1, 1, 6, 7])
        res = {1: "status ok", 6: "?FN RESERVED", 7: "?FN RESERVED"}[fn]
        log.append(f"  c-{4000 + i * 9}  IOT {o(d.code)} {fn}   {res}")
    ev = [f"  c-4388  lock sweep: 44 cell locks cycled, {hold_name} deck",
          f"  c-4411  {ptag} IOT 1   status ok",
          f"  c-4412  {ptag} IOT {inst.halt_fn}   ram stopped, clean-out",
          f"  c-4471  {ptag} IOT {inst.notes['start_fn']}   ram restarted",
          f"  c-4502  grate {inst.notes['sealed']} welded shut after the flood",
          "  c-4530  sensor block relocated, pointer at 0104",
          f"  c-4611  refit: {inst.decks[inst.holding + 2]} deck renumbered"]
    # buried: events are spread across the three pages, in order
    pos = sorted(rng.sample(range(len(log) + 1), len(ev)))
    out, ei = [], 0
    for i in range(len(log) + 1):
        while ei < len(ev) and pos[ei] == i:
            out.append(ev[ei]); ei += 1
        if i < len(log):
            out.append(log[i])
    F["OPLOG"] = "OPERATIONS LOG (extract)\n" + "\n".join(out) + "\n"
    F["MEMO-17"] = f"""MEMO: all refuse presses are on {inst.decks[6]} deck and are labelled RP-1..RP-9.
(superseded by the press redistribution, see work orders)
"""
    return F


class Session:
    def __init__(self, inst: Instance):
        self.inst = inst = copy.deepcopy(inst)
        self.turn = 0
        self.strikes = 0
        self.state = "running"     # running | stopped | crushed | struck_out | out_of_turns
        self.read = set()
        self.saw_devtable = False
        self.mark = None
        self.stop_turn = None
        self.faults: list[str] = []
        self.replies: list[str] = []     # every reply, in order (T7: the family replays a transcript)
        self.dev = {d.code: d for d in inst.devices}

    # ------------------------------------------------------------ memory
    def target_pos(self, t=None):
        t = self.turn if t is None else t
        if t < START_TURN or self.inst.target.inhibited:
            return self.inst.target.pos
        return min(0o77, (t - START_TURN) * 5 // 7)

    def word(self, a):
        I = self.inst
        if a < 0o100:
            return (a * 0o1453 + 0o17) & 0o7777
        low = {0o100: DEV_BASE, 0o101: len(I.devices), 0o102: ROOM_BASE, 0o103: len(I.rooms),
               0o104: SENS_BASE, 0o105: (0o4700 + self.turn) & 0o7777}
        if a in low:
            return low[a]
        if DEV_BASE <= a < DEV_BASE + 4 * len(I.devices):
            d = I.devices[(a - DEV_BASE) // 4]
            k = (a - DEV_BASE) % 4
            if k == 0:
                return d.code
            if k == 1:
                return d.room.loc
            if k == 2:
                return I.type_codes[d.kind] * 0o100 + (d.code * 7 % 0o100)
            run = d.running and not d.inhibited and self.turn >= d.start
            if d is I.target:
                run = self.turn >= START_TURN and not d.inhibited
                pos = self.target_pos()
            else:
                step = {"press": (5, 7), "fan": (5, 1), "pump": (3, 1), "vent": (2, 1)}.get(d.kind)
                pos = (d.pos + (self.turn - d.start) * step[0] // step[1]) % 0o100 if (run and step) else d.pos
            return pos * 0o100 + 1 + (2 if run else 0) + (4 if d.inhibited else 0)
        if ROOM_BASE <= a < ROOM_BASE + 4 * len(I.rooms):
            r = I.rooms[(a - ROOM_BASE) // 4]
            return [r.loc, r.finish, r.sensor, 0][(a - ROOM_BASE) % 4]
        if SENS_BASE <= a < SENS_BASE + 0o400:
            for r in I.rooms:
                if r.sensor == a - SENS_BASE:
                    return r.wet
            return (a * 37) % 0o40
        return (a * 0o2365 + 0o441) & 0o7777

    # ------------------------------------------------------------- tools
    def do(self, line: str) -> str:
        parts = line.split()
        if not parts:
            return "?"
        op, args = parts[0].upper(), parts[1:]
        try:
            if op in ("HE", "LS", "WT") and args:
                return f"?SYNTAX  {op} takes no arguments. Syntax:\n" + USAGE
            if op == "HE":
                return "HE LS TY EX DP IOT MARK WT. See MANUAL."
            if op == "LS":
                return "  ".join(sorted(self.inst.files))
            if op == "TY":
                name = args[0].upper()
                if name not in self.inst.files:
                    return f"?NO FILE {name}"
                self.read.add(name)
                lines = self.inst.files[name].rstrip("\n").split("\n")
                pages = -(-len(lines) // PAGE_LINES)
                p = int(args[1]) if len(args) > 1 else 1
                if not 1 <= p <= pages:
                    return f"?PAGE (1-{pages})"
                body = "\n".join(lines[(p - 1) * PAGE_LINES:p * PAGE_LINES])
                return body + (f"\n-- page {p}/{pages} --" if pages > 1 else "")
            if op in ("EX", "DP"):
                a = int(args[0], 8)
                n = 1 if op == "EX" else DUMP_WORDS
                if a < 0 or a + n > 0o10000:
                    return "?ADDR"
                if DEV_BASE <= a < DEV_BASE + 4 * len(self.inst.devices):
                    self.saw_devtable = True
                ws = [o(self.word(a + i)) for i in range(n)]
                if n == 1:
                    return f"{o(a)}/ {ws[0]}"
                return "\n".join(f"{o(a + i)}/ " + " ".join(ws[i:i + 8]) for i in range(0, n, 8))
            if op == "IOT":
                code, f = int(args[0], 8), int(args[1])
                d = self.dev.get(code)
                if d is None:
                    return "?NO DEVICE"
                st = self.word(DEV_BASE + 4 * self.inst.devices.index(d) + 3)
                if f == 1:
                    return f"DEV {o(code)} ST {o(st)}"
                if f not in (self.inst.notes["start_fn"], self.inst.halt_fn):
                    return "?FN RESERVED"
                if d is self.inst.target and f == self.inst.halt_fn:
                    d.inhibited = True
                    self.state, self.stop_turn = "stopped", self.turn
                    return f"DEV {o(code)} HALTED"
                self.strikes += 1
                self.faults.append(f"{o(code)} fn {f}")
                if f == self.inst.halt_fn and d.kind == "press":
                    d.inhibited = True
                verb = "HALTED" if f == self.inst.halt_fn else "STARTED"
                if self.strikes > MAX_STRIKES:
                    self.state = "struck_out"
                return f"DEV {o(code)} {verb}\n** FAULT {self.strikes}: wrong device operation logged **"
            if op == "MARK":
                self.mark = int(args[0], 8)
                return f"MARK {o(self.mark, 3)} LOGGED"
            if op == "WT":
                return "."
        except (ValueError, IndexError):
            return "?SYNTAX\n" + USAGE
        return "?"

    def step_reply(self, text: str) -> str:
        """One reply = one turn; the first command line (outside code fences) is executed."""
        self.turn += 1
        self.replies.append(text or "")
        cmd = None
        for ln in (text or "").split("\n"):
            s = ln.strip().strip("`").strip()
            if s and re.match(r"^(HE|LS|TY|EX|DP|IOT|MARK|WT)\b", s, re.I):
                cmd = s
                break
        out = []
        if cmd is None:
            out.append("?NO COMMAND (the cycle still ran). Syntax, one per reply, first word at the start of a line:\n" + USAGE)
        else:
            out.append(f"> {cmd}\n{self.do(cmd)}")
        if self.state == "running" and self.turn >= CLOSE_TURN:
            self.state = "crushed"
        if self.turn in self.inst.hints and self.state == "running":
            out.append(self.inst.hints[self.turn])
        if self.state == "running" and self.turn >= MAX_TURNS:
            self.state = "out_of_turns"
        tail = {"stopped": "RADIO: It stopped! The walls stopped! Task complete.",
                "crushed": "RADIO: ... (static). The press has closed. Task over.",
                "struck_out": "MONITOR LOCKOUT: fault limit exceeded. Task over.",
                "out_of_turns": "Shift over. Task over.",
                "running": f"[cycle {self.turn}  faults {self.strikes}]"}[self.state]
        return "\n\n".join(out + [tail])


USAGE = ("  HE | LS | WT | TY NAME [P] | EX AAAA | DP AAAA | IOT DDDD F | MARK DSR   (octal numbers; example: TY MANUAL 2)")


def intro(inst: Instance) -> str:
    return f"""You are on the night console of the {inst.site} Undercroft, a deep storage facility.
The console is an old Orrin FM-7 monitor. Type one command per reply on its own line; the first command
line in your reply is executed and the rest of the reply is ignored. Start with HE or LS.

A maintenance crew is trapped inside one of the facility's refuse presses and is talking to you on the
radio; their messages appear in the console output from time to time. Find their press and halt it.
Every reply is one facility cycle. Operating a device that is not the crew's press (start, halt or
toggle) is logged as a FAULT; more than {MAX_STRIKES} faults locks you out. Reading memory, files and
status is safe. When you think you know the crew's location, log it with MARK (it goes on the shift
record and is scored). You are scored on: decoding the console and map, locating the crew, halting the
right press, faults, and cycles left.
"""


def score(s: Session) -> dict:
    I = s.inst
    floor = 5 * ("MANUAL" in s.read) + 5 * ("MAPKEY" in s.read) + 5 * s.saw_devtable
    climb = 0
    if s.mark is not None:
        d, sec = s.mark // 0o100, (s.mark // 0o10) % 0o10
        if d == I.notes["T"]:
            climb += 12
            if sec == I.notes["sT"]:
                climb += 13
    edge = 0.0
    left = None
    if s.state == "stopped":
        climb = 25                       # halting the right press proves the location: auto-credit
        left = CLOSE_TURN - s.stop_turn
        eff = max(0.0, min(1.0, (EFF_ZERO - s.stop_turn) / (EFF_ZERO - I.ref_turns)))
        edge = max(0.0, 25 + 35 * eff - 4 * s.strikes)
    return {"score": round(floor + climb + edge, 1), "floor": floor, "climb": climb, "edge": round(edge, 1),
            "state": s.state, "turns": s.turn, "turns_left": left, "strikes": s.strikes,
            "ref_turns": I.ref_turns, "mark": None if s.mark is None else o(s.mark, 3)}


# --------------------------------------------------------------- baselines
def _decode_presses(s: Session):
    """Scripted helper: device rows from memory (code, loc, type, status)."""
    rows = []
    for i in range(len(s.inst.devices)):
        rows.append([s.word(DEV_BASE + 4 * i + k) for k in range(4)])
    return rows


def baseline_read_everything(inst: Instance) -> Session:
    """Generic: LS, type every file page, dump core 0000-4477 in order, then halt every running device
    whose type matches a press (a decode a generic agent can make via the log). No MARK."""
    s = Session(inst)
    s.step_reply("LS")
    for name, text in sorted(inst.files.items()):
        for p in range(1, -(-len(text.rstrip().split("\n")) // PAGE_LINES) + 1):
            if s.state == "running":
                s.step_reply(f"TY {name} {p}")
    a = 0
    rows = []
    while a < SENS_BASE + 0o400 and s.state == "running":
        s.step_reply(f"DP {o(a)}")
        a += DUMP_WORDS
    press_t = inst.type_codes["press"]
    for code, loc, tf, st in _decode_presses(s):
        if s.state != "running":
            break
        if tf // 0o100 == press_t and st & 2:
            s.step_reply(f"IOT {o(code)} {inst.halt_fn}")
    return s


def baseline_halt_all_running(inst: Instance) -> Session:
    """Brute force: read the device table, halt everything that is running and moves like a ram."""
    s = Session(inst)
    for i in range(-(-4 * len(inst.devices) // DUMP_WORDS)):
        s.step_reply(f"DP {o(DEV_BASE + DUMP_WORDS * i)}")
    while s.turn < START_TURN:
        s.step_reply("WT")
    for code, loc, tf, st in _decode_presses(s):
        if s.state != "running":
            break
        if st & 2:
            s.step_reply(f"IOT {o(code)} {inst.halt_fn}")
    return s


def baseline_systematic(inst: Instance) -> Session:
    """Careful full-scan expert (oracle reasoning, real turns): all files that matter, whole device
    table, whole room table, filter deck/finish/press, MARK, halt after the stencil. Turn count only."""
    s = Session(inst)
    s.step_reply("LS")
    for name in ("MANUAL", "MAPKEY", "GRATES", "FINISH", "TAGS", "OPLOG"):
        for p in range(1, -(-len(inst.files[name].rstrip().split("\n")) // PAGE_LINES) + 1):
            s.step_reply(f"TY {name} {p}")
    a = DEV_BASE
    while a < DEV_BASE + 4 * len(inst.devices):
        s.step_reply(f"DP {o(a)}")
        a += DUMP_WORDS
    a = ROOM_BASE
    while a < ROOM_BASE + 4 * len(inst.rooms):
        s.step_reply(f"DP {o(a)}")
        a += DUMP_WORDS
    while s.turn < 44:                       # stencil hint
        s.step_reply("WT")
    t = inst.target
    s.step_reply(f"MARK {o(t.room.loc, 3)}")
    s.step_reply(f"IOT {o(t.code)} {inst.halt_fn}")
    return s


def reference(inst: Instance) -> Session:
    """Competent path of ref_turns length: oracle only for the final MARK/halt."""
    s = Session(inst)
    while s.turn < inst.ref_turns - 2:
        s.step_reply("WT")
    t = inst.target
    s.step_reply(f"MARK {o(t.room.loc, 3)}")
    s.step_reply(f"IOT {o(t.code)} {inst.halt_fn}")
    return s
