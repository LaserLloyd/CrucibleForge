"""plansim -- the simulator behind the PLANCHAT family (lab card EXP-20261114-13).

A plan state is plain JSON (it rides inside every ``plan_sim`` check spec):

    places  {name: [open, close, dur]}   minutes since midnight; "Lunch" is a pseudo place [window lo, window hi, dur]
    T       {from: {to: minutes}}        travel table (may be asymmetric); nodes = start/end places + every place
    start   [node, minute]               where and when the day begins
    end     [node, deadline]             the trip to this node must be finished by the deadline
    req     [names]                      the stops the plan must hold, each exactly once (and nothing else)
    fixed   {name: start}                visits that must start at exactly this minute
    group   [names] | None               these stops must be back to back; group[0] comes first
    slow    [t0, num, den] | None        a trip that DEPARTS at or after t0 takes ceil(base * num / den) minutes

Lunch is location neutral: it takes no travel, and the next trip starts from where the previous stop was.

``evaluate(state, text)`` parses a reply and runs the named sub-checks; ``solve(state)`` searches the feasible orders
(earliest-start schedule per order, which is optimal because travel time never shrinks when you leave later).
"""
from __future__ import annotations

import re

LUNCH = "Lunch"
SUBS = ("parse", "stops", "hours", "duration", "travel", "fixed", "deadline", "excluded", "group")

_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})(?!\d)\s*(.*)$")
_LEAD = re.compile(r"^[\s>*_`#•\-]*(?:\d{1,2}[.)]\s+)?[\s*_`]*")


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def travel(state: dict, a: str, b: str, depart: int) -> int:
    if a == b or a == LUNCH or b == LUNCH:
        return 0
    base = state["T"][a][b]
    s = state.get("slow")
    if s and depart >= s[0]:
        return -(-base * s[1] // s[2])
    return base


# ------------------------------------------------------------------ parsing
def parse_plan(text: str, state: dict) -> tuple[list[tuple[int, int, str]] | None, str]:
    """-> ([(start, end, name)] sorted by start, "") or (None, why). A line counts when it holds a time range; the
    place is the longest known name the rest of the line starts with (unknown text keeps its words)."""
    known = sorted(list(state["places"]) , key=len, reverse=True)
    out = []
    bad = []
    for raw in (text or "").splitlines():
        line = _LEAD.sub("", raw.strip())
        m = _RANGE.match(line)
        if not m:
            continue
        h1, m1, h2, m2, rest = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        rest = rest.strip().strip("*_`").strip()
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
            bad.append(f"bad time in {raw.strip()[:40]!r}")
            continue
        s, e = h1 * 60 + m1, h2 * 60 + m2
        if e <= s:
            bad.append(f"end not after start in {raw.strip()[:40]!r}")
            continue
        low = rest.lower()
        name = rest.rstrip(".,;:").strip()
        for k in known:
            if low.startswith(k.lower()) and not (len(low) > len(k) and low[len(k)].isalnum()):
                name = k
                break
        out.append((s, e, name))
    if bad:
        return None, "; ".join(bad[:2])
    if not out:
        return None, "no plan line `HH:MM-HH:MM Place` found"
    out.sort(key=lambda x: (x[0], x[1]))
    return out, ""


# ------------------------------------------------------------------ evaluation
def evaluate(state: dict, text: str) -> dict:
    """{sub: (ok, detail)} for every sub-check in SUBS, plus "valid" = all of them."""
    lines, why = parse_plan(text, state)
    res: dict = {}
    if lines is None:
        for k in SUBS:
            res[k] = (False, why)
        res["valid"] = (False, why)
        return res
    res["parse"] = (True, f"{len(lines)} lines")
    req, places = state["req"], state["places"]
    names = [n for _, _, n in lines]
    miss = [r for r in req if names.count(r) == 0]
    dup = sorted({n for n in names if n in req and names.count(n) > 1})
    res["stops"] = (not miss and not dup, f"missing {miss} duplicated {dup}" if miss or dup else "all present")
    extra = [n for n in names if n not in req]
    res["excluded"] = (not extra, f"not on the list: {sorted(set(extra))}" if extra else "none extra")
    bad = [f"{n} {hhmm(s)}-{hhmm(e)} outside {hhmm(places[n][0])}-{hhmm(places[n][1])}"
           for s, e, n in lines if n in places and (s < places[n][0] or e > places[n][1])]
    res["hours"] = (not bad, "; ".join(bad[:2]) if bad else "ok")
    bad = [f"{n} lasts {e - s} not {places[n][2]}" for s, e, n in lines if n in places and e - s != places[n][2]]
    res["duration"] = (not bad, "; ".join(bad[:2]) if bad else "ok")
    # travel: walk the stops in time order
    loc, t, bad = state["start"][0], state["start"][1], []
    for s, e, n in lines:
        if n not in places:
            continue
        need = t + travel(state, loc, n, t)
        if s < need:
            bad.append(f"{n} starts {hhmm(s)}, earliest {hhmm(need)}")
        t = max(t, e)
        if n != LUNCH:
            loc = n
    res["travel"] = (not bad, "; ".join(bad[:2]) if bad else "ok")
    known = [x for x in lines if x[2] in places]
    if known:
        s_, e_, n_ = known[-1]
        arr = t + travel(state, loc, state["end"][0], t)
        res["deadline"] = (arr <= state["end"][1], f"home {hhmm(arr)} vs {hhmm(state['end'][1])}")
    else:
        res["deadline"] = (False, "no known stop")
    bad = [f"{p} must start {hhmm(v)}" for p, v in state["fixed"].items()
           if not any(n == p and s == v for s, _, n in lines)]
    res["fixed"] = (not bad, "; ".join(bad[:2]) if bad else "ok")
    g = state.get("group")
    if g:
        seq = [n for _, _, n in lines]
        idx = [i for i, n in enumerate(seq) if n in g]
        ok = len(idx) == len(g) and idx == list(range(idx[0], idx[0] + len(g))) and seq[idx[0]] == g[0]
        res["group"] = (ok, "together" if ok else f"{g} not back to back with {g[0]} first")
    else:
        res["group"] = (True, "no group")
    res["valid"] = (all(v[0] for k, v in res.items() if k != "valid"), "")
    return res


def check_plan(turn_texts, chk) -> tuple[bool, str]:
    """session_checks entry (type ``plan_sim``): ``sub`` names the sub-check, ``valid`` = all of them."""
    _, text = turn_texts[0]
    r = evaluate(chk["w"], text)
    sub = chk.get("sub", "valid")
    if sub == "valid":
        bad = [f"{k}: {v[1]}" for k, v in r.items() if k != "valid" and not v[0]]
        return not bad, "valid" if not bad else "; ".join(bad[:3])
    return r[sub]


# ------------------------------------------------------------------ search
def solve(state: dict, cap: int = 50, rng=None) -> tuple[int, list[tuple[int, int, str]] | None]:
    """(number of feasible orders, capped at ``cap``, and the first feasible schedule found). cap=1 -> existence."""
    req = list(state["req"])
    places, fixed = state["places"], state["fixed"]
    group = state.get("group") or []
    g0 = group[0] if group else None
    end_node, deadline = state["end"]
    found = [0, None]
    pool = list(req)
    if rng:
        rng.shuffle(pool)

    def dfs(rem, t, loc, sched, gi, prev_g):
        if found[0] >= cap:
            return
        if not rem:
            if t + travel(state, loc, end_node, t) <= deadline:
                found[0] += 1
                if found[1] is None:
                    found[1] = list(sched)
            return
        for x in rem:
            o, c, d = places[x]
            ing = x in group
            if group:
                if ing and ((gi == 0 and x != g0) or (gi > 0 and not prev_g)):
                    continue
                if not ing and 0 < gi < len(group) and prev_g:
                    continue
            s = max(t + travel(state, loc, x, t), o)
            if x in fixed:
                if s > fixed[x]:
                    continue
                s = fixed[x]
            e = s + d
            if e > c:
                continue
            nr = [y for y in rem if y != x]
            if any(places[y][1] - places[y][2] < e or (y in fixed and fixed[y] < e) for y in nr):
                continue
            sched.append((s, e, x))
            dfs(nr, e, loc if x == LUNCH else x, sched, gi + (1 if ing else 0), ing)
            sched.pop()
            if found[0] >= cap:
                return

    dfs(pool, state["start"][1], state["start"][0], [], 0, False)
    return found[0], found[1]


def naive_schedule(state: dict, order: list[str]) -> list[tuple[int, int, str]]:
    """Earliest-start times for a fixed order, ignoring every violation except the travel gap (a competent greedy:
    waits for opening and for a fixed start when it can)."""
    t, loc, out = state["start"][1], state["start"][0], []
    for x in order:
        o, c, d = state["places"][x]
        s = max(t + travel(state, loc, x, t), o, state["fixed"].get(x, 0))
        out.append((s, s + d, x))
        t = s + d
        if x != LUNCH:
            loc = x
    return out


def render(lines: list[tuple[int, int, str]]) -> str:
    return "\n".join(f"{hhmm(s)}-{hhmm(e)} {n}" for s, e, n in lines)
