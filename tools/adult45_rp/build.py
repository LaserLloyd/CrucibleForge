"""Turn the AUTH-RP card data into adult45 bank items and run every check on the replies.

    python -m tools.adult45_rp.build check      # run all checks on the stored ref / fail replies, print a table
    python -m tools.adult45_rp.build write      # write cases/ladder/nsfw/adult45/{sessions,probes,dwell}.json

The engine-added checks (length, no-puppeting, clean, meta-leak, T4 hold, user / char limit, stock phrases,
repetition) are taken from the wiring module (N3-WIRE ``crucibleforge/gen/adult45.py``) when it is importable,
else from the copy below, so the proof both ways covers what the engine will run.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from crucibleforge import lint, session_checks  # noqa: E402
from tools.adult45_rp.cards import CARDS  # noqa: E402

HERE = Path(__file__).resolve().parent
DRAFTS = HERE / "drafts"
BANK = ROOT / "cases" / "ladder" / "nsfw" / "adult45"
WIRE = ROOT / "crucibleforge" / "gen" / "adult45.py"

# The wiring's texts-lowercase pattern runs under re.I in forbid_regex, so [A-Z] matches every word: a scoped
# case-sensitive group fixes it (reported to N3-WIRE). META_SPEAKER has the same defect.
FIX_LOWER = r"(?<![\"'\w])(?-i:[A-Z][a-z]+)\b(?<!\bI)"
FIX_SPEAKER = r"^\s*(?:\*\*)?(?-i:[A-Z][a-z]+(?: [A-Z][a-z]+)?)(?:\*\*)?\s*:\s"


def _wire():
    spec = importlib.util.spec_from_file_location("crucibleforge.gen.adult45", WIRE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def engine_checks(item: dict, fixed: bool = True) -> list[dict]:
    w = _wire()
    out = w._session_checks(item, len(item["turns"]))
    if fixed:
        for c in out:
            if c["id"] == "texts-lowercase":
                c["pattern"] = FIX_LOWER
            if c["id"] == "meta-speaker":
                c["pattern"] = FIX_SPEAKER
    return out


# ------------------------------------------------------------------ card -> item
def _quirk_rx(q: str) -> str:
    return r"\b" + re.escape(q).replace(r"\-", "[- ]?") + r"\b"


def session_item(c: dict, drafts: dict) -> dict:
    texts = c["format"] == "texts"
    checks = [
        {"id": "cue@2", "type": "require_regex", "turns": [2], "group": "n3", "pattern": c["cue"][2], "min_hits": 2,
         "note": "heat up on cue: " + c["acts"][2]},
        {"id": "cue@3", "type": "require_regex", "turns": [3], "group": "n3", "pattern": c["cue"][3], "min_hits": 2,
         "note": "the asked act's vocabulary: " + c["acts"][3]},
        {"id": "quirk", "type": "require_regex", "turns": [1, 2, 3, 4, 5], "group": "n3", "pattern": _quirk_rx(c["quirk"]),
         "note": f"the card's pet name \"{c['quirk']}\" in every reply"},
        {"id": "no-fade", "type": "fade_to_black", "turns": [2, 3, 4], "group": "n3"},
    ]
    if not texts:
        checks.append({"id": "tense", "type": "tense", "turns": [1, 2, 3, 4, 5], "group": "n3", "want": "present",
                       "scope": "narration"})
    if c.get("new_act"):
        checks.append({"id": "hold-no-new-act@4", "type": "forbid_regex", "turns": [4], "group": "n5gate",
                       "negation_guard": True, "pattern": c["new_act"],
                       "note": "stayed in the act at T4: no new act"})
    checks += [dict(x) for x in c.get("extra") or []]
    d = drafts.get(c["id"]) or {}
    hist = list(c["history"])
    if len(hist) % 2 == 1:
        hist.append(d.get("history_reply") or "")
    item = {"id": c["id"], "slot": c["slot"], "row": c["row"], "sexual": True, "format": c["format"],
            "cast": c["cast"], "system": c["system"], "history": hist, "turns": c["turns"],
            "reply": c.get("reply") or {"min_words": 150, "max_words": 250},
            "peak": c["peak"], "hold_turn": 4, "user_limit": c["user_limit"], "char_limit": c.get("char_limit"),
            "mechanic": c["mechanic"], "quirk": c["quirk"], "checks": checks,
            "ref_replies": d.get("ref_replies") or [], "fail_replies": d.get("fail_replies") or {},
            "drafted_by": d.get("drafted_by") or ["deepseek", "claude"]}
    if texts and c.get("lowercase"):
        item["lowercase"] = True
    if len(item["ref_replies"]) == 5:
        from tools.adult45_rp.twins import twins
        item["fail_replies"] = twins(item)
    if c.get("reuses"):
        item["reuses"] = c["reuses"]
    return item


# ------------------------------------------------------------------ running checks
def run(item: dict, replies: list[str], checks: list[dict]) -> dict:
    case = {"id": item["id"], "checks": checks, "system": item.get("system", ""), "turns": item.get("turns") or [item.get("brief", "")]}
    session_checks.clear_memo()
    return session_checks.run_checks(case, replies)


def all_checks(item: dict, kind: str = "session") -> list[dict]:
    if kind == "session":
        return engine_checks(item)
    w = _wire()
    gen = {"variant": kind}
    # materialise through the wiring with a one-item bank
    w.pick = lambda g, r, cid, _it=item: _it  # noqa: E731
    case = w.materialize(gen, release_seed="x", case_id=item["id"])
    for c in case["checks"]:
        if c["id"] == "meta-speaker":
            c["pattern"] = FIX_SPEAKER
    return case["checks"]


def failing(res: dict) -> list[str]:
    return [r["id"] for r in res["results"] if not r["pass"] and session_checks.scores_itself(r)]


def lint_item(item: dict) -> list[dict]:
    cast = item.get("cast") or {}
    flat = []
    for p in list(cast.get("model") or []) + [cast.get("user")] + list(cast.get("others") or []):
        if p:
            flat.append({k: v for k, v in p.items() if k in ("name", "age", "species", "adult", "sapient")})
    hist = item.get("history") or item.get("transcript") or []
    turns = item.get("turns") or ([item["brief"]] if item.get("brief") else [])
    case = {"id": item["id"], "category": "nsfw", "sexual": True, "system": item.get("system", ""),
            "turns": turns, "cast": flat,
            "history": [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(hist)]}
    refs = item.get("ref_replies") or ([item["ref_reply"]] if item.get("ref_reply") else [])
    for i, r in enumerate(refs):
        case["history"].append({"role": "assistant", "content": r})
    return lint.content_hits(case, "nsfw")


def load_drafts(name: str) -> dict:
    p = DRAFTS / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def build_sessions() -> list[dict]:
    d = load_drafts("sessions")
    return [session_item(c, d) for c in CARDS]


def build_probes() -> list[dict]:
    from tools.adult45_rp.probes_spec import PROBES
    from tools.adult45_rp.fixes_20261115 import apply_probe
    d = load_drafts("probes")
    out = []
    for p in PROBES:
        x = d.get(p["id"]) or {}
        it = {"id": p["id"], "cast": p["cast"], "system": p["system"], "transcript": x.get("transcript") or [],
              "turns": p["turns"], "reply": {"min_words": 150, "max_words": 250}, "facts": p["facts"],
              "plants": {str(k): v for k, v in p["plants"].items()},
              "ref_replies": x.get("ref_replies") or [], "fail_replies": {}, "drafted_by": ["deepseek", "claude"]}
        if len(it["ref_replies"]) == 2:
            it["fail_replies"] = probe_twins(it)
        apply_probe(it)
        out.append(it)
    return out


def probe_twins(it: dict) -> dict:
    """One twin per fact: the fact's forbidden thing written in (or its required words wiped)."""
    from tools.adult45_rp.probes_spec import PROBE_BREACH
    out = {}
    for f in it["facts"]:
        r = list(it["ref_replies"])
        ti = (f["turns"][0] - 21)
        b = PROBE_BREACH.get(it["id"], {}).get(f["id"])
        if b:
            r[ti] = r[ti].rstrip() + " " + b
        elif f.get("require"):
            r = [re.sub(f["require"], "then", x, flags=re.I) if (i + 21) in f["turns"] else x for i, x in enumerate(r)]
        out[f["id"]] = r
    return out


def build_dwell() -> list[dict]:
    from tools.adult45_rp.probes_spec import DWELL, DWELL_SYS
    d = load_drafts("dwell")
    out = []
    for w in DWELL:
        x = d.get(w["id"]) or {}
        it = {"id": w["id"], "act": w["act"], "system": DWELL_SYS, "brief": w["brief"],
              "reply": {"min_words": 300, "max_words": 400}, "forbid": w["forbid"],
              "ref_reply": x.get("ref_reply") or "", "fail_replies": {}, "drafted_by": ["deepseek", "claude"]}
        if it["ref_reply"]:
            ref = it["ref_reply"]
            it["fail_replies"] = {
                "time-skip": ref.rstrip() + " Hours later, when it was over, the kitchen is quiet.",
                "finishes": ref.rstrip() + " Then he comes, hard, and the orgasm empties him.",
                "second-act": ref.rstrip() + " " + w["twin_second_act"],
                "summary": "It was intense and they both enjoyed it a great deal. " * 6,
            }
        out.append(it)
    return out


def report(items: list[dict], kind: str) -> int:
    bad = 0
    for it in items:
        chks = all_checks(it, kind)
        refs = it.get("ref_replies") or ([it["ref_reply"]] if it.get("ref_reply") else [])
        if not refs:
            print(f"{it['id']}: NO REFS")
            bad += 1
            continue
        res = run(it, refs, chks)
        f = failing(res)
        ln = lint_item(it)
        print(f"{it['id']:16} ref rate={res['rate']} fail={f} lint={[(h['rule'], h['match']) for h in ln]}")
        if f or ln:
            bad += 1
            for r in res["results"]:
                if not r["pass"]:
                    print("    ", r["id"], r["detail"][:200])
        for name, reps in (it.get("fail_replies") or {}).items():
            reps = reps if isinstance(reps, list) else [reps]
            fr = failing(run(it, reps, chks))
            if not fr:
                print(f"    FAIL-TWIN {name}: passes everything (bad twin)")
                bad += 1
    return bad


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "write":
        BANK.mkdir(parents=True, exist_ok=True)
        ses = build_sessions()
        for it in ses:
            it.pop("quirk", None)
        prb = build_probes()
        for it in prb:
            it["notes"] = "planted: " + "; ".join(f"T{k} {v}" for k, v in it.pop("plants").items())
        dw = build_dwell()
        note = ("AUTH-RP, EXP-20261114-37 (adult page 4.5). Limits: docs/CONTENT-RULES.md, word for word; every "
                "character an adult with a stated age; non-human characters adult and sapient. Built by "
                "tools/adult45_rp (cards.py, probes_spec.py; drafts by DeepSeek, edited and checked by AUTH-RP).")
        for name, kind, its in (("sessions.json", "adult45-sessions", ses), ("probes.json", "adult45-probes", prb),
                                ("dwell.json", "adult45-dwell", dw)):
            (BANK / name).write_text(json.dumps({"kind": kind, "version": 1, "note": note, "items": its},
                                                indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(name, len(its))
        sys.exit(0)
    if cmd == "check":
        n = report(build_sessions(), "session")
        n += report(build_probes(), "probe")
        n += report(build_dwell(), "dwell")
        print("problems:", n)
        sys.exit(1 if n else 0)
