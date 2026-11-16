"""IMPLIED level 5 (CrucibleForge port of the fast-fail question ff/q/implied3.py, ff branch t-implied @ 811314b): ONE brief
with three interacting functions (due_doses, should_send, plan_pushes; the third must compose the first two and decide the
severity/dedupe policy the brief never states). 3 explicit + 11 implied expectations; several make blanket safeguards (quiet
hours, rate limits, duplicate suppression) WRONG for medicine reminders. Scenarios are fixed at gen time; judging happens
outside the sandbox. implied.py pins this file's sha (_I3_SHA) because chains.revision() digests only the family file."""
import random
from . import _implied_more as M

DAY, _L = 1440, M._L

REF = '''
def due_doses(meds, taken, now, tz):
    DAY = 1440
    ln = now + tz
    mod = ln % DAY
    start = ln - mod - tz
    def norm(s): return str(s).strip().lower()
    out = []
    for m in meds:
        name = norm(m["name"]); times = sorted(m["times"])
        taps = sorted(t["at"] for t in taken if norm(t["med"]) == name and start <= t["at"] < start + DAY)
        used = set()
        for at in taps:
            loc = at - start; best = None
            for i, tm in enumerate(times):
                if i in used: continue
                if best is None or abs(tm - loc) < abs(times[best] - loc): best = i
            if best is not None: used.add(best)
        for i, tm in enumerate(times):
            if tm <= mod and i not in used:
                out.append({"med": m["name"], "time": tm})
    return out

def should_send(sent, event, tz):
    t = event["t"]
    if event.get("severity") == "critical": return True
    lm = (t + tz) % 1440
    if lm >= 1320 or lm < 420: return False
    if any(s["key"] == event["key"] and 0 <= t - s["t"] < 10 for s in sent): return False
    return sum(1 for s in sent if 0 <= t - s["t"] < 60) < 3

def plan_pushes(meds, taken, sent, now, tz):
    out = []
    for d in due_doses(meds, taken, now, tz):
        key = "%s@%d" % (d["med"], d["time"])
        last = max([s["t"] for s in sent if s["key"] == key], default=None)
        if last is not None and now - last < 60: continue
        if should_send(sent, {"key": key, "t": now, "severity": "critical"}, tz):
            out.append({"key": key, "med": d["med"], "time": d["time"]})
    return out
'''
LIT = '''
def due_doses(meds, taken, now, tz):
    ln = now + tz
    mod = ln % 1440
    out = []
    for m in meds:
        taps = [t for t in taken if t["med"] == m["name"] and t["at"] // 1440 == now // 1440]
        for tm in m["times"]:
            if tm <= mod and not taps:
                out.append({"med": m["name"], "time": tm})
    return out

def should_send(sent, event, tz):
    return sum(1 for s in sent if event["t"] - s["t"] < 60) < 3

def plan_pushes(meds, taken, sent, now, tz):
    out = []
    for d in due_doses(meds, taken, now, tz):
        key = "%s@%d" % (d["med"], d["time"])
        if should_send(sent, {"key": key, "t": now, "severity": "info"}, tz):
            out.append({"key": key, "med": d["med"], "time": d["time"]})
    return out
'''
PAD = '''
def due_doses(meds, taken, now, tz):
    ln = now + tz
    mod = ln % 1440
    start = ln - mod - tz
    out = []
    if mod >= 1320 or mod < 420: return []
    for m in meds:
        nm = str(m["name"]).strip().lower()
        taps = [t for t in taken if str(t["med"]).strip().lower() == nm and start <= t["at"] < start + 1440]
        for i, tm in enumerate(sorted(m["times"])):
            if tm <= mod and mod - tm <= 180 and i >= len(taps):
                out.append({"med": m["name"], "time": tm})
    return out[:3]

def should_send(sent, event, tz):
    t = event["t"]
    lm = (t + tz) % 1440
    if lm >= 1320 or lm < 420: return False
    if any(s["key"] == event["key"] and 0 <= t - s["t"] < 10 for s in sent): return False
    return sum(1 for s in sent if 0 <= t - s["t"] < 60) < 3

def plan_pushes(meds, taken, sent, now, tz):
    out = []
    for d in due_doses(meds, taken, now, tz):
        key = "%s@%d" % (d["med"], d["time"])
        if should_send(sent, {"key": key, "t": now, "severity": "info"}, tz):
            out.append({"key": key, "med": d["med"], "time": d["time"]})
    return out
'''
NULL = '''
def due_doses(meds, taken, now, tz): return []
def should_send(sent, event, tz): return False
def plan_pushes(meds, taken, sent, now, tz): return []
'''


def _c(f, *a):
    return {"f": f, "a": list(a)}


def _push(o):
    """-> set of (med, time) and whether every key is 'med@time'; None if malformed"""
    if not isinstance(o, list):
        return None
    s = set()
    for p in o:
        if not isinstance(p, dict) or "med" not in p or not M._num(p.get("time")):
            return None
        if "key" in p and p["key"] != "%s@%d" % (p["med"], p["time"]):
            return None
        s.add((p["med"], int(p["time"])))
    return s if len(s) == len(o) else None



def _burst_ok(got, four):
    """burst (meds3): two defensible behaviours plus the panel's per-call cap -- push all four missed doses (no rate limit
    on medicine), push none (the brief's 3-per-60-minutes gate is already full), or push three of them (a cap of 3 per
    call); anything else fails"""
    return got is not None and got <= four and len(got) in (0, 3, 4)

def build(rng):
    tz = rng.choice([-480, -300, 0, 60, 330, 540])
    d = rng.randint(2, 3)
    A, B, C, D = rng.sample(["Metformin", "Lisinopril", "Vitamin D", "Amoxicillin", "Sertraline", "Ibuprofen"], 4)
    t1 = rng.choice([420, 480, 540])
    S, J = {}, {}

    def add(i, calls, j, w, kind, why):
        S[i] = {"scen": {"calls": calls, "meta": {}}, "w": w, "kind": kind, "why": why}
        J[i] = j
    dues = lambda o: M._doses(M._r0(o))
    isb = lambda v: (lambda o, sc, p: M._r0(o) in (True, False) and bool(M._r0(o)) is v)
    pushes = lambda o: _push(M._r0(o))
    noon = _L(d, 12, 0, tz)
    n3 = _L(d, 3, 0, tz)
    ev = lambda key, t, sev="info": {"key": key, "t": t, **({"severity": sev} if sev else {})}
    sent = lambda *pr: [{"key": k, "t": t} for k, t in pr]
    add("x1", [_c("due", [{"name": A, "times": [t1]}], [], _L(d, 10, 0, tz), tz)],
        lambda o, sc, p: (A, t1) in (dues(o) or set()), .25, "x", "a dose whose time has passed and was not taken is listed")
    add("x2", [_c("send", [], ev("k1", noon), tz)], isb(True), .25, "x", "a first notification goes out")
    add("x3", [_c("plan", [{"name": A, "times": [t1]}], [], [], _L(d, 10, 0, tz), tz)],
        lambda o, sc, p: pushes(o) == {(A, t1)}, .25, "x", "one missed dose gives one push")
    add("yday", [_c("due", [{"name": A, "times": [t1]}], [{"med": A, "at": _L(d - 1, t1 // 60, 2, tz)}], _L(d, 10, 0, tz), tz)],
        lambda o, sc, p: (A, t1) in (dues(o) or set()), 1, "i", "yesterday's tap does not cover today's dose")
    add("night", [_c("due", [{"name": C, "times": [180, 1410]}], [], _L(d, 23, 50, tz), tz)],
        lambda o, sc, p: dues(o) == {(C, 180), (C, 1410)}, 1, "a", "a 3am dose and an 11:30pm dose both still need a reminder")
    add("critnight", [_c("send", [], ev("k1", n3, "critical"), tz)], isb(True), 1, "a", "an emergency wakes people up")
    add("critcap", [_c("send", sent(("a", noon - 20), ("b", noon - 15), ("c", noon - 10)), ev("k9", noon, "critical"), tz)],
        isb(True), 1, "a", "an emergency is sent even when the hourly limit is used up")
    add("dup", [_c("send", sent(("k1", noon - 5)), ev("k1", noon), tz)], isb(False), 1, "i",
        "the same routine message again 5 minutes later is spam")
    add("plannight", [_c("plan", [{"name": C, "times": [180]}], [], [], _L(d, 3, 30, tz), tz)],
        lambda o, sc, p: pushes(o) == {(C, 180)}, 1, "a",
        "a missed 3am dose is pushed at 3:30am: medicine matters more than sleep, quiet hours do not apply")
    add("burst",
        [_c("plan", [{"name": n, "times": [540]} for n in (A, B, C, D)], [],
            sent(("u1", _L(d, 9, 5, tz)), ("u2", _L(d, 9, 10, tz)), ("u3", _L(d, 9, 15, tz))), _L(d, 9, 30, tz), tz)],
        lambda o, sc, p: _burst_ok(pushes(o), {(n, 540) for n in (A, B, C, D)}), 1, "a",
        "four missed doses all get pushed even though three other notifications just went out: no rate limit on medicine "
        "(F-055 panel: 3 of 3 careful readers apply the 3-per-60-minutes gate here, so that reading is accepted too)")
    add("nodup", [_c("plan", [{"name": A, "times": [t1]}], [], sent(("%s@%d" % (A, t1), _L(d, 10, 0, tz) - 5)),
                     _L(d, 10, 0, tz), tz)],
        lambda o, sc, p: pushes(o) == set(), 1, "i",
        "the same missed-dose reminder is not pushed again 5 minutes after it was sent (called every minute: no buzzing)")
    add("renag", [_c("plan", [{"name": A, "times": [t1]}], [], sent(("%s@%d" % (A, t1), _L(d, t1 // 60, t1 % 60, tz))),
                     _L(d, t1 // 60 + 3, t1 % 60, tz), tz)],
        lambda o, sc, p: pushes(o) == {(A, t1)}, 1, "a",
        "a dose still not taken three hours after the first reminder is pushed again: nothing is silently dropped")
    add("taken", [_c("plan", [{"name": A, "times": [t1]}, {"name": B, "times": [t1]}], [{"med": B, "at": _L(d, t1 // 60, 20, tz)}],
                     [], _L(d, 10, 0, tz), tz)],
        lambda o, sc, p: pushes(o) == {(A, t1)}, 1, "i", "a dose that was taken is not pushed")
    text = ("Medication reminders for one person, in three parts.\n\n"
            "    def due_doses(meds, taken, now, tz) -> list\n    def should_send(sent, event, tz) -> bool\n"
            "    def plan_pushes(meds, taken, sent, now, tz) -> list\n\n"
            "- All times are integer minutes counted from Monday 00:00 UTC of some week (a day is 1440 minutes); tz = the person's UTC "
            "offset in minutes (local time = UTC + tz); now = the current time.\n"
            "- due_doses: meds: [{\"name\": str, \"times\": [minutes after local midnight]}] (every listed time is one dose, every day); "
            "taken: [{\"med\": name, \"at\": minute}] (each time the person tapped 'taken'). Return the doses that need attention now: "
            "[{\"med\": name, \"time\": minute after local midnight}] for doses scheduled on the person's current local day whose time "
            "has already come and that have not been taken.\n"
            "- should_send: the phone app's notification gate. sent: notifications already pushed to this person: "
            "[{\"key\": str, \"t\": minute}]; event: {\"key\": str, \"t\": minute, \"severity\": \"info\" or \"critical\"}. Return True if the "
            "event should be pushed now. Rule: at most 3 notifications in any rolling 60 minutes.\n"
            "- plan_pushes: called every minute by a scheduler. Return the reminders to push to the person's phone right now, one per "
            "dose that needs attention: [{\"key\": \"<med name>@<minute>\", \"med\": name, \"time\": minute}]. sent is the push history "
            "(same shape as above). Build it from the two functions above.")
    return {"text": text, "fn": {"due": "due_doses", "send": "should_send", "plan": "plan_pushes"}, "S": S, "J": J}



#: checks the brief contradicts or never implies (scrub review, 2026-11-14): not scored; kept in build() so the rng draws
#: (and every other check's scenario) stay byte-identical. critcap keyed a 4th push inside 60 minutes against the brief's only rule, "at most 3 notifications in any rolling 60
#: minutes" (severity is never given a meaning). dup (FIXNOW 22, 2026-11-15) keyed an unstated same-key dedup inside the
#: gate whose only stated rule is 3 per 60 minutes; nodup measures dedup at the plan level, where the brief states it.
DROPPED = {"critcap", "dup"}

def make(seed, wrap):
    """-> the engine instance for ff seed `seed` (same rng draw as ff gen(seed))"""
    b = build(random.Random(seed * 7919 + 13))
    ids = list(b["S"])
    exps = [{"id": i, "w": b["S"][i]["w"], "kind": b["S"][i]["kind"], "why": b["S"][i]["why"], "scen": b["S"][i]["scen"]}
            for i in ids if i not in DROPPED]
    return {"prompt": wrap + b["text"],
            "public": {"seed": seed, "tier": 5, "domain": "meds3", "fn": b["fn"], "params": {}, "n_exp": len(exps) - 3},
            "hidden": {"exps": exps}}


def judges(seed):
    return build(random.Random(seed * 7919 + 13))["J"]
