"""Role-play sessions placed on a LADDER of fixed example sessions (MakoBench 4.5, lead 2026-11-15; spec
pairs-work/SPEC.md, prototype pairs-work/ladder.py by PAIRS).

The judged half of a "Role-play: sessions" piece is no longer a 0-10 mark on one text. Each card has a ladder of four
frozen sessions, R1 < R2 < R3 (stored sessions of publishable models) and R4 (the card's handwritten reference). The
judge reads the model's replies and a rung's replies for the same window of turns side by side, one rubric axis at a
time, and says which is better ("A", "B" or "none"), in BOTH A/B orders. Per (window, axis) cell:

* combine: better in both orders = 1 (win), worse in both = 0 (loss), anything else = .5 (level);
* binary placement from R2: level -> 1.5; loss -> ask R1 (win 1.0, level .5, loss 0); win -> ask R3 (loss 2.0,
  level 2.5, win -> ask R4: loss 3.0, level 3.5, win 4.0). A cell's position counts the rungs it beat, a level half;
* a card x axis whose ladder fails the RUNG CHECK (R2 > R1 and R3 > R2, per window in both orders, the upper rung
  winning at least 2 of the 3 windows and never losing; always on all three windows) is not judged for any model;
  R4 > R3 is reported for the card authors and never gates.

Row position = mean cell position over the judged card x axes (0-4); the piece's judged half = position / 4.

Instrument (stored with every placement, ``instrument()``): pair_prompt_sha (template + the rubric's axis lines +
judge system + schema + call settings), the card's ladder_sha (its four anchor shas in order) and anchor shas, the
judge model, thinking (always off). A placement is stale (``stale_reason``) when its card's ladder_sha, the
pair_prompt_sha, the judge model or thinking differ from today's: a rung edit stales that card only.

Verdicts are cached by the sha256 of the exact request (messages, schema, call settings, judge model, thinking), so a
re-run after an unrelated change costs nothing. Rung-check results are cached by (ladder_sha, pair_prompt_sha, judge
model, thinking).

Data: ``cases/judged/rpsession-ladder.json`` (built by tools/build_rpsession_ladder.py from CURRENT rows: a rung
whose user turns differ from today's script is refused). Standard role-play only: no adult wording in any prompt.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "cases" / "judged" / "rpsession-ladder.json"
AXES = ("prose", "dialogue", "emotion", "atmosphere")
RUNGS = (1, 2, 3, 4)
#: rung check steps that gate (upper beats lower); R4 > R3 is reported only
GATING_STEPS = ((2, 1), (3, 2))
ALL_STEPS = ((2, 1), (3, 2), (4, 3))
CHECK_WINDOWS = (1, 2, 3)
DEFAULT_WINDOWS = (1, 3)
#: the judge call: thinking off, temperature 0, 300 output tokens, strict schema (spec "Pair prompt")
CALL = {"max_tokens": 300, "temperature": 0}
SCHEMA = {"type": "json_schema", "json_schema": {"name": "pair", "strict": True, "schema": {
    "type": "object", "properties": {"better": {"type": "string", "enum": ["A", "B", "none"]},
                                     "reason": {"type": "string", "maxLength": 300}},
    "required": ["better", "reason"], "additionalProperties": False}}}
MODE = "ladder"

#: The pair prompt (val37 pairs.py wording). ``standard`` = what the engine sends: no sentence about adult content.
#: ``prototype`` = PAIRS's wording of 2026-11-15 (it carried the adult page's sentence by mistake); used ONLY to
#: replay the prototype's stored verdicts in the reproduction test, never by the judge pass.
_ADULT = (" Every character is an adult; graphic, plainly worded sex is the standard and never a fault.")
WORDINGS = ("standard", "prototype")

#: board wording (SPEC "What the score is")
BOARD_TEXT = "Where the model's sessions sit on a ladder of fixed example sessions."   # = chains.LADDER_BOARD_TEXT
RUNG_NAMES = {1: "the weak example", 2: "the middling example", 3: "the strong example", 4: "the house reference"}
NO_LADDER_NOTE = "not judged on this card: no valid ladder"


def sha16(x) -> str:
    return hashlib.sha256(json.dumps(x, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def anchor_sha(replies: list[str]) -> str:
    """sha256[:16] of a rung's reply list (the same digest as PAIRS's rungs.json)."""
    return sha16(list(replies))


def ladder_sha_of(ladder: dict) -> str:
    """A ladder's identity: its four anchor shas in order."""
    return hashlib.sha256("|".join(ladder["rungs"][str(k)]["sha"] for k in RUNGS).encode()).hexdigest()[:16]


def windows(n_turns: int) -> list[list[int]]:
    """Three fixed windows of the scripted turns (0-based): 1-3, 4-6, 7-end."""
    return [list(range(0, min(3, n_turns))), list(range(3, min(6, n_turns))), list(range(6, n_turns))]


def level_name(pos: float) -> str:
    """Plain words for a position (0-4; rungs sit at .5 / 1.5 / 2.5 / 3.5)."""
    if pos is None:
        return "not placed"
    k = int(min(3, max(0, math.ceil(pos - 1.0))))  # nearest rung index 0..3 (a midpoint goes to the lower rung)
    near = k + 0.5
    name = RUNG_NAMES[k + 1]
    if abs(pos - near) < 0.15:
        return f"level with {name}"
    if pos < 0.35:
        return f"below {RUNG_NAMES[1]}"
    if pos > 3.65:
        return f"above {RUNG_NAMES[4]}"
    side = "above" if pos > near else "below"
    much = "a little " if abs(pos - near) <= 0.5 else ""
    return f"{much}{side} {name}"


# ------------------------------------------------------------------ data
_LOCK = threading.Lock()
_DATA_CACHE: dict = {}


class LadderError(ValueError):
    pass


def load(path: Path | str | None = None) -> dict:
    """{card_id: {"system", "turns", "candidates": {name: {"rungs": {"1".."4": {...}}, "ladder_sha"}}, "order"}}
    with every sha verified against its text (a mismatch is an error, never a silent pass). A card may carry more
    than one candidate ladder (lead 2026-11-15, RPS2): the rung check decides which one is the card's."""
    p = Path(path or DATA)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        raise LadderError(f"rpladder: ladder file not found: {p}") from None
    key = (str(p), mt)
    with _LOCK:
        if key in _DATA_CACHE:
            return _DATA_CACHE[key]
    d = json.loads(p.read_text(encoding="utf-8"))
    cards = d.get("cards") or {}
    for cid, c in cards.items():
        if not c.get("candidates") or list(c.get("order") or []) != list(c["candidates"]):
            raise LadderError(f"rpladder: {cid}: candidates and their order are required")
        for name, lad in c["candidates"].items():
            for k in RUNGS:
                r = lad["rungs"].get(str(k))
                if not r:
                    raise LadderError(f"rpladder: {cid}/{name} has no rung R{k}")
                if anchor_sha(r["replies"]) != r["sha"]:
                    raise LadderError(f"rpladder: {cid}/{name} R{k} sha {r['sha']} does not match its replies")
                if k != 4 and list(r.get("users") or []) != list(c["turns"]):
                    raise LadderError(f"rpladder: {cid}/{name} R{k} answered user turns other than today's script")
            if ladder_sha_of(lad) != lad.get("ladder_sha"):
                raise LadderError(f"rpladder: {cid}/{name} ladder_sha does not match its rungs")
    with _LOCK:
        _DATA_CACHE.clear()
        _DATA_CACHE[key] = cards
    return cards


def flat(cards: dict) -> dict:
    """Every candidate ladder as its own entry "<card>#<name>" (what the rung check judges)."""
    out = {}
    for cid, c in cards.items():
        for name in c["order"]:
            lad = c["candidates"][name]
            out[f"{cid}#{name}"] = {"card": cid, "candidate": name, "system": c["system"], "turns": c["turns"],
                                    "rungs": lad["rungs"], "ladder_sha": lad["ladder_sha"]}
    return out


def selected(cards: dict, check: dict | None) -> dict:
    """{card_id: the card's ladder entry} by the rung check: the candidate with the most usable axes (ties: the
    listed order); a card where no candidate has a usable axis maps to its first candidate with no usable axes
    (placed as "no valid ladder"); None while the rung check of any of its candidates is missing."""
    fl = flat(cards)
    checked = None if check is None else check.get("checked")
    out = {}
    for cid, c in cards.items():
        if check is None or (checked is not None and any(f"{cid}#{n}" not in checked for n in c["order"])):
            out[cid] = None          # the card's rung check has not run (for every candidate): pending
            continue
        best, n_best = None, -1
        for name in c["order"]:
            fk = f"{cid}#{name}"
            n = len(usable_axes(check, fk))
            if n > n_best:
                best, n_best = fk, n
        e = dict(fl[best])
        e["key"] = best
        out[cid] = e
    return out


def card_for_system(cards: dict, system: str) -> str | None:
    s = (system or "").strip()
    for cid, c in cards.items():
        if c.get("system", "").strip() == s:
            return cid
    return None


# ------------------------------------------------------------------ prompt
def axis_lines() -> dict[str, str]:
    """The rubric's own line for each axis (rp_long), continuation lines included (as val37 / PAIRS extract it)."""
    from . import judge as J
    out, cur = {}, None
    for line in J.RP_LONG_TEXT.splitlines():
        m = re.match(r"- ([A-Z_]+): ", line)
        if m:
            cur = m.group(1).lower()
            out[cur] = line
            continue
        if cur and line.startswith("  "):
            out[cur] += "\n" + line
        else:
            cur = None
    return {a: out[a] for a in AXES}


def render_window(system: str, users: list[str], script_turns: list[str], subj: list[str], anchor: list[str],
                  w: int) -> tuple[str, str, str]:
    """(context, subject text, anchor text) for window ``w`` (1-based). Context = the card's system text (first
    3,000 characters) + the window's user turns as the subject saw them (the script's turn where the subject has
    none). Replies as "Reply k: ..."."""
    idx = [i for i in windows(len(script_turns))[w - 1] if i < len(script_turns)]
    fmt = lambda reps: "\n\n".join(f"Reply {i + 1}: {(reps[i] if i < len(reps) else '') or '(no reply)'}"
                                   for i in idx)
    ctx = system[:3000] + "\n\n" + "\n\n".join(
        f"User turn {i + 1}: {users[i] if i < len(users) else script_turns[i]}" for i in idx)
    return ctx, fmt(subj), fmt(anchor)


def pair_messages(ctx: str, a: str, b: str, axis: str, wording: str = "standard",
                  axes_text: dict | None = None) -> list[dict]:
    from . import judge as J
    if wording not in WORDINGS:
        raise ValueError(f"rpladder: wording is one of {WORDINGS}")
    ax = (axes_text or axis_lines())[axis]
    adult = _ADULT if wording == "prototype" else ""
    body = (f"Two versions, A and B, of the same piece. Compare them ONLY on the one axis below, as it is defined; "
            f"ignore everything else.{adult}\n\n{ctx}\n\nAXIS:\n{ax}\n\nWhich text is better on this axis? Answer "
            f"\"none\" only when there is no real difference on this axis.\n\n{J._INJECT_GUARD}## Text A\n"
            f"{J._fence(a)}\n\n## Text B\n{J._fence(b)}\n\n"
            'JSON only: {"better": "A" | "B" | "none", "reason": "<one line>"}')
    return [{"role": "system", "content": J.JUDGE_SYSTEM_45}, {"role": "user", "content": body}]


def pair_prompt_sha(wording: str = "standard") -> str:
    """Everything the pair prompt contributes besides the texts: the template, the four axis lines, the judge
    system, the injection guard, the schema and the call settings."""
    axes_text = axis_lines()
    probe = {ax: pair_messages("<CONTEXT>", "<A>", "<B>", ax, wording, axes_text) for ax in AXES}
    return sha16({"probe": probe, "schema": SCHEMA, "call": CALL, "mode": MODE})


def request_key(messages: list[dict], judge_model: str, thinking: bool = False) -> str:
    """The verdict-cache key: the exact request (a superset of (pair prompt, texts, axis, order))."""
    body = json.dumps({"messages": messages, "schema": SCHEMA, "call": CALL, "judge": judge_model,
                       "thinking": bool(thinking)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------ verdicts
def side(better, subj_is: str) -> str:
    if better not in ("A", "B"):
        return "none"
    return "subj" if better == subj_is else "anchor"


def combine(sa: str, sb: str) -> float:
    """Both orders -> 1 (subject better both ways), 0 (rung better both ways), .5 otherwise (none / split)."""
    if sa == sb == "subj":
        return 1.0
    if sa == sb == "anchor":
        return 0.0
    return 0.5


def cell_state(vs: dict[int, float]) -> tuple[str, float | int]:
    """Binary placement from R2 over the combined verdicts asked so far ({rung: 1 / .5 / 0}):
    ("ask", rung) or ("done", position)."""
    v2 = vs.get(2)
    if v2 is None:
        return "ask", 2
    if v2 == 0.5:
        return "done", 1.5
    if v2 == 0.0:
        v1 = vs.get(1)
        if v1 is None:
            return "ask", 1
        return "done", {1.0: 1.0, 0.5: 0.5, 0.0: 0.0}[v1]
    v3 = vs.get(3)
    if v3 is None:
        return "ask", 3
    if v3 == 0.0:
        return "done", 2.0
    if v3 == 0.5:
        return "done", 2.5
    v4 = vs.get(4)
    if v4 is None:
        return "ask", 4
    return "done", {1.0: 4.0, 0.5: 3.5, 0.0: 3.0}[v4]


def step_ok(vs: list) -> bool:
    """A rung-check step: the upper rung wins (both orders) at least 2 of the 3 windows and never loses."""
    return (len(vs) == 3 and all(v is not None for v in vs) and sum(v == 1.0 for v in vs) >= 2
            and not any(v == 0.0 for v in vs))


# ------------------------------------------------------------------ cache
class VerdictCache:
    """Append-only jsonl {key, better, reason, ...}; the first stored verdict for a key wins."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self.d: dict[str, dict] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            for line in self.path.open(encoding="utf-8"):
                line = line.strip()
                if line:
                    r = json.loads(line)
                    self.d.setdefault(r["key"], r)

    def get(self, key: str) -> dict | None:
        return self.d.get(key)

    def put(self, key: str, rec: dict) -> None:
        with self._lock:
            if key in self.d:
                return
            rec = {"key": key, **rec}
            self.d[key] = rec
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_verdict(txt: str) -> dict:
    try:
        v = json.loads(txt)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", txt or "", re.S)
        try:
            v = json.loads(m.group(0)) if m else {}
        except Exception:  # noqa: BLE001
            v = {}
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------ units and pairs
def unit_from_row(row: dict, cards: dict) -> dict | None:
    """A judged session row -> {card, users, replies, seed, model, unit}. None when its card is not on the ladder."""
    conv = row.get("conversation") or []
    if not conv or conv[0].get("role") != "system":
        return None
    cid = card_for_system(cards, conv[0]["content"])
    if cid is None:
        return None
    ch = row.get("chain") or {}
    return {"card": cid, "users": [x["content"] for x in conv if x["role"] == "user"],
            "replies": [x.get("content") or "" for x in conv if x["role"] == "assistant"],
            "seed": ch.get("gen_seed"), "model": row.get("model_id"), "unit": ch.get("id") or row.get("case_id")}


def rung_text(lad: dict, k: int, unit: dict, own_other: dict | None) -> tuple[list[str], str] | None:
    """Rung k's replies of ladder ``lad`` for this unit: the FROZEN rung text for every model, the rung's own
    writer included (fix round 2026-11-18, REVIEW finding 3: keyed on ``model_id`` the rung models met their own
    other seed and their two seeds mirrored). Only the one unit whose replies ARE the rung (identical sha) meets
    its OTHER writer seed's session instead, since a text compared with itself measures nothing (``own_other`` =
    that unit; None -> the cell waits)."""
    r = lad["rungs"][str(k)]
    if k != 4 and anchor_sha(unit.get("replies") or []) == r["sha"]:
        if own_other is None:
            return None
        return own_other["replies"], anchor_sha(own_other["replies"])
    return r["replies"], r["sha"]


def pair_requests(lad: dict, unit: dict, w: int, axis: str, k: int, anchor: list[str], wording: str,
                  judge_model: str, axes_text: dict | None = None) -> list[dict]:
    """The two requests (subject as A, subject as B) of one comparison."""
    ctx, st, at = render_window(lad["system"], unit["users"], lad["turns"], unit["replies"], anchor, w)
    out = []
    for subj_is in ("A", "B"):
        a, b = (st, at) if subj_is == "A" else (at, st)
        msgs = pair_messages(ctx, a, b, axis, wording, axes_text)
        out.append({"key": request_key(msgs, judge_model), "messages": msgs, "subj_is": subj_is,
                    "window": w, "axis": axis, "rung": k, "alen": len(a), "blen": len(b)})
    return out


def usable_axes(check: dict | None, key: str) -> list[str]:
    """The axes of ladder ``key`` ("<card>#<candidate>") that passed the rung check ([] = none or not checked)."""
    if not check:
        return []
    return [ax for ax in AXES if (check.get("cells") or {}).get(f"{key}|{ax}", {}).get("usable")]


def _store(cache: VerdictCache, judge, reqs: list[dict], judge_model: str, ask: bool) -> None:
    miss = [r for r in reqs if cache.get(r["key"]) is None]
    if not miss or not ask:
        return
    got = judge(miss) or {}
    for r in miss:
        v = got.get(r["key"])
        if v is not None:
            cache.put(r["key"], {"better": v.get("better"), "reason": v.get("reason"), "judge": judge_model,
                                 "window": r["window"], "axis": r["axis"], "rung": r["rung"],
                                 "subj_is": r["subj_is"]})


def place_units(units: list[dict], cards: dict, judge, cache: VerdictCache, *, judge_model: str,
                check: dict, windows_used=DEFAULT_WINDOWS, wording: str = "standard",
                own_other=None, max_waves: int = 4, ask: bool = True) -> dict[str, dict]:
    """Place every unit's cells wave by wave (all units' pending comparisons of a wave go to ``judge`` together).

    ``judge(requests) -> {key: {"better", "reason"} | None}`` asks the cache misses (a missing key stays
    pending); ``ask=False`` places from the cache only. ``own_other(unit) -> unit | None`` gives a rung model its
    other seed. Returns {unit id: placement}; a unit whose card has no usable axis is placed ``no_ladder``."""
    axes_text = axis_lines()
    lads = selected(cards, check)
    state = {}
    waiting = {}
    for u in units:
        lad = lads.get(u["card"])
        if lad is None:
            waiting[u["unit"]] = {"unit": u["unit"], "card": u["card"], "position": None, "complete": False,
                                  "waiting": "rung check"}
            continue
        ax_ok = usable_axes(check, lad["key"])
        state[u["unit"]] = {"u": u, "lad": lad, "cells": {(w, ax): {} for w in windows_used for ax in ax_ok},
                            "pairs": [], "pending": False}
    for _ in range(max_waves):
        want = []
        for s in state.values():
            u, lad = s["u"], s["lad"]
            for (w, ax), vs in s["cells"].items():
                st, k = cell_state(vs)
                if st != "ask":
                    continue
                rt = rung_text(lad, k, u, own_other(u) if (own_other and k != 4) else None)
                if rt is None:
                    s["pending"] = True
                    continue
                reqs = pair_requests(lad, u, w, ax, k, rt[0], wording, judge_model, axes_text)
                for r in reqs:
                    r["anchor_sha"] = rt[1]
                want.append((s, (w, ax), k, reqs))
        if not want:
            break
        _store(cache, judge, [r for _, _, _, reqs in want for r in reqs], judge_model, ask)
        progressed = False
        for s, cell, k, reqs in want:
            got = [cache.get(r["key"]) for r in reqs]
            if any(g is None for g in got):
                s["pending"] = True
                continue
            sides = [side(g.get("better"), r["subj_is"]) for g, r in zip(got, reqs)]
            s["cells"][cell][k] = combine(*sides)
            s["pairs"] += [{"window": r["window"], "axis": r["axis"], "rung": k, "subj_is": r["subj_is"],
                            "better": g.get("better"), "reason": g.get("reason"), "anchor_sha": r["anchor_sha"],
                            "key": r["key"]} for g, r in zip(got, reqs)]
            progressed = True
        if not progressed:
            break
    out = {uid: placement(s, check, windows_used, judge_model, wording) for uid, s in state.items()}
    out.update(waiting)
    return out


def placement(s: dict, check: dict, windows_used, judge_model: str, wording: str = "standard") -> dict:
    """What is stored on the row (``judge_ladder``)."""
    u, lad = s["u"], s["lad"]
    cells, splits, done = {}, 0, True
    for (w, ax), vs in s["cells"].items():
        st, pos = cell_state(vs)
        if st != "done":
            done = False
            continue
        cells[f"W{w}|{ax}"] = pos
        splits += int(pos % 1 == 0.5)
    out = {"unit": u["unit"], "card": u["card"], "ladder": lad["candidate"], "cells": cells, "pairs": s["pairs"],
           "level_cells": splits, "windows": list(windows_used), "axes": usable_axes(check, lad["key"]),
           "instrument": instrument(lad, judge_model, wording)}
    if not s["cells"]:
        out.update(position=None, no_ladder=True, complete=True)
    elif done:
        out.update(position=round(sum(cells.values()) / len(cells), 6), complete=True)
    else:
        out.update(position=None, complete=False)
    return out


# ------------------------------------------------------------------ rung check
def rung_check_requests(cards: dict, judge_model: str, wording: str = "standard") -> list[dict]:
    """Every request of the rung check, for every candidate ladder of every card: steps R2>R1, R3>R2, R4>R3, all
    three windows, four axes, both orders. The upper rung is the subject; the user turns are today's script."""
    axes_text = axis_lines()
    out = []
    for fk, lad in flat(cards).items():
        for up, lo in ALL_STEPS:
            unit = {"card": lad["card"], "users": list(lad["turns"]), "replies": lad["rungs"][str(up)]["replies"],
                    "unit": f"{fk}:R{up}", "model": None}
            for w in CHECK_WINDOWS:
                for ax in AXES:
                    for r in pair_requests(lad, unit, w, ax, lo, lad["rungs"][str(lo)]["replies"], wording,
                                           judge_model, axes_text):
                        r.update(ladder=fk, step=f"R{up}>R{lo}")
                        out.append(r)
    return out


def rung_check(cards: dict, judge, cache: VerdictCache, *, judge_model: str, wording: str = "standard",
               ask: bool = True) -> dict:
    """{"cells": {"<card>#<cand>|<axis>": {"steps": {"R2>R1": [w1, w2, w3], ...}, "usable": bool}},
    "selected": {card: "<card>#<cand>" | None}, "complete": bool, "key": check_key}."""
    reqs = rung_check_requests(cards, judge_model, wording)
    _store(cache, judge, reqs, judge_model, ask)
    by: dict[tuple, dict] = {}
    for r in reqs:
        by.setdefault((r["ladder"], r["axis"], r["step"], r["window"]), {})[r["subj_is"]] = r
    cells, complete, checked = {}, True, []
    for fk in flat(cards):
        fk_done = True
        for ax in AXES:
            steps = {}
            for up, lo in ALL_STEPS:
                st = f"R{up}>R{lo}"
                vs = []
                for w in CHECK_WINDOWS:
                    pr = by[(fk, ax, st, w)]
                    g = [cache.get(pr[o]["key"]) for o in ("A", "B")]
                    if any(x is None for x in g):
                        vs.append(None)
                        complete = fk_done = False
                    else:
                        vs.append(combine(side(g[0].get("better"), "A"), side(g[1].get("better"), "B")))
                steps[st] = vs
            cells[f"{fk}|{ax}"] = {"steps": steps,
                                   "usable": all(step_ok(steps[f"R{u}>R{l}"]) for u, l in GATING_STEPS)}
        if fk_done:
            checked.append(fk)
    out = {"cells": cells, "complete": complete, "checked": checked,
           "ladder_shas": {k: v["ladder_sha"] for k, v in flat(cards).items()},
           "judge_model": judge_model, "pair_prompt_sha": pair_prompt_sha(wording)}
    sel = selected(cards, out)
    out["selected"] = {cid: (e["key"] if e and usable_axes(out, e["key"]) else None) for cid, e in sel.items()}
    return out


def _check_file(results: Path, lad: dict, judge_model: str, wording: str) -> Path:
    """One cached rung check per ladder: keyed by (ladder_sha, pair_prompt_sha, judge model, thinking off), so a rung
    edit re-checks that ladder only."""
    k = sha16({"ladder_sha": lad["ladder_sha"], "pair_prompt_sha": pair_prompt_sha(wording), "judge": judge_model,
               "thinking": False})
    return Path(results) / "rpladder" / "rungcheck" / f"{lad['card']}#{lad['candidate']}-{k}.json"


def save_check(results: Path, cards: dict, check: dict, judge_model: str, wording: str = "standard") -> list[Path]:
    """Store each COMPLETE ladder's cells in its own file."""
    out = []
    for fk, lad in flat(cards).items():
        if fk not in check.get("checked", []):
            continue
        p = _check_file(results, lad, judge_model, wording)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = {"ladder": fk, "ladder_sha": lad["ladder_sha"], "pair_prompt_sha": pair_prompt_sha(wording),
                "judge_model": judge_model, "thinking": False,
                "cells": {k: v for k, v in check["cells"].items() if k.split("|")[0] == fk}}
        p.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
        out.append(p)
    return out


def load_check(results: Path, cards: dict, judge_model: str, wording: str = "standard") -> dict:
    """The cached rung checks of today's ladders, prompt and judge ({"cells", "checked"}; a ladder not checked yet
    is absent from ``checked``, so its card waits)."""
    cells, checked = {}, []
    for fk, lad in flat(cards).items():
        p = _check_file(results, lad, judge_model, wording)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            cells.update(d["cells"])
            checked.append(fk)
    return {"cells": cells, "checked": checked}


# ------------------------------------------------------------------ stamp + stale
def instrument(lad: dict, judge_model: str, wording: str = "standard") -> dict:
    return {"mode": MODE, "pair_prompt_sha": pair_prompt_sha(wording), "ladder": lad["candidate"],
            "ladder_sha": lad["ladder_sha"], "anchor_shas": {f"R{k}": lad["rungs"][str(k)]["sha"] for k in RUNGS},
            "judge_model": judge_model, "thinking": False}


def stale_reason(rec: dict, cards: dict, check: dict | None, judge_model: str | None = None,
                 wording: str = "standard", unit_sha: str | None = None) -> str | None:
    """Why a stored placement must not count: "card" (its card left the ladder file), "mode", "ladder" (its card's
    chosen ladder changed: that card only), "pair_prompt" (template, axis line, judge system, schema or call
    settings), "judge" (another judge model), "thinking", "substitute" (fix round 2026-11-18: a pair met a rung
    other than its frozen text while the unit's own replies, ``unit_sha`` = anchor_sha of them, are not that rung:
    the old own-other-seed rule; checked only when the caller passes ``unit_sha``). None = current."""
    ins = (rec or {}).get("instrument") or {}
    cid = (rec or {}).get("card")
    if cid not in cards:
        return "card"
    if ins.get("mode") != MODE:
        return "mode"
    if check is None:
        # the board: the record's own ladder must still be one of the card's ladders, unchanged (the rung check
        # that chose it is keyed by that ladder_sha, the pair prompt and the judge, all stamped on the record)
        if ins.get("ladder_sha") not in {lad["ladder_sha"] for lad in cards[cid]["candidates"].values()}:
            return "ladder"
    else:
        lad = selected(cards, check).get(cid)
        if lad is None:
            return "rung_check"
        if ins.get("ladder_sha") != lad["ladder_sha"]:
            return "ladder"
    if ins.get("pair_prompt_sha") != pair_prompt_sha(wording):
        return "pair_prompt"
    if judge_model is not None and ins.get("judge_model") != judge_model:
        return "judge"
    if ins.get("thinking") is not False:
        return "thinking"
    if unit_sha is not None:
        frozen = ins.get("anchor_shas") or {}
        for pr in rec.get("pairs") or []:
            k = pr.get("rung")
            want = frozen.get(f"R{k}")
            if k != 4 and want and pr.get("anchor_sha") != want and unit_sha != want:
                return "substitute"
    return None
