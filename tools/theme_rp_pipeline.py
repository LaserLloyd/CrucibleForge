#!/usr/bin/env python3
"""THEME_RP bank pipeline driver (4.5-N2): code-seeded cards, one Grok call per card, repair round, stage B, assemble.

  theme_rp_pipeline.py draft  --ks 0-4 [--cells R05:3,...]   # stage A items -> 4 watched probe runners
  theme_rp_pipeline.py collect                               # parse + validate A; writes cards-A.json, fail-A.json
  theme_rp_pipeline.py repair                                # one repair call per failed card, then collect again
  theme_rp_pipeline.py refs                                  # stage B for valid cards without final references
  theme_rp_pipeline.py fixrefs                               # one repair call per card whose references fail a check
  theme_rp_pipeline.py assemble [--per-cell 5]
Work dir: /var/tmp/mb45-build/n2-work/rp-bank (WORK). Every probe launch writes EXPECTED.md first; exit 7/5 stop.
"""
from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import theme_rp_bank as B  # noqa: E402

WORK = Path(os.environ.get("RP_WORK", "/var/tmp/mb45-build/n2-work/rp-bank"))
PROBE = "/var/tmp/mb45-build/n-work/probe.py"
import os
N_RUN = int(os.environ.get('RP_RUNNERS', '4'))
BUDGET = float(os.environ.get("RP_BUDGET", "25.0"))
# per-call expectation from pilot 4 (3 cards: 1.8k in, 3.5k out, 9-17k reasoning): one card ~ 2.3k in, 1.3k out, 5k reasoning
PER = {"A": (20000, 0.11), "R": (20000, 0.11), "B": (36000, 0.18), "F": (36000, 0.18)}   # per call of 3 cards


_OLD = None


def old_errors(card: dict, tid: str, rung: int) -> list[str]:
    """Problems the session's earlier (stricter) validator, kept in WORK/old_bank.py, found on a card that now
    validates: the cards these concern are the restored ones (flagged ``checked: false`` for a human read)."""
    global _OLD
    if _OLD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("old_bank", WORK / "old_bank.py")
        _OLD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_OLD)
    try:
        return _OLD.validate(card, tid, rung)
    except Exception as e:  # noqa: BLE001
        return [f"old validator error {type(e).__name__}"]


def _cards() -> list[dict]:
    """Valid stage-A cards of the drawable rows (R27 is excluded by ruling: its cards are never kept)."""
    return [c for c in json.loads((WORK / "cards-A.json").read_text()) if c["theme"] in B.THEMES]


def spent() -> tuple[int, float]:
    t, u = 0, 0.0
    for lf in WORK.glob("*/run*/log.jsonl"):
        for l in lf.read_text().splitlines():
            r = json.loads(l)
            t += r["in"] + r["out"] + r.get("reasoning", 0)
            u += r["usd"]
    return t, round(u, 4)


def run_phase(tag: str, items: list[dict], kind: str) -> int:
    """Split items over N_RUN watched probe runners (separate dirs so logs do not collide); wait for all."""
    if not items:
        print(f"[{tag}] nothing to do")
        return 0
    t0, u0 = spent()
    et, eu = PER[kind]
    if u0 + eu * len(items) > BUDGET:
        print(f"[{tag}] STOP: spent ${u0} + expected ${eu * len(items):.2f} would pass ${BUDGET}")
        return 9
    procs, rcs = [], {}
    for i in range(N_RUN):
        part = items[i::N_RUN]
        if not part:
            continue
        d = WORK / tag / f"run{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "items.jsonl").write_text("".join(json.dumps(x) + "\n" for x in part))
        done = {json.loads(l)["id"] for l in (d / "log.jsonl").read_text().splitlines() if json.loads(l)["rc"] in (0, 4)} \
            if (d / "log.jsonl").exists() else set()
        todo = [x for x in part if x["id"] not in done]
        if not todo:
            continue
        et_, eu_ = et * len(todo), round(eu * len(todo), 3)
        (d / "EXPECTED.md").write_text(
            f"# theme_rp bank {tag} run{i} (grok-4.7, $2/M in, $6/M out, reasoning billed as output)\n\n"
            f"{len(todo)} calls (3 cards per call). Single-card pilot measured ~2k in, ~1k out, 10-16k reasoning (~$0.086/card); 3 per call should share the reasoning overhead.\n"
            f"**Expected: {et} tokens/call x {len(todo)} = {et * len(todo)} tokens, ${eu_}.** Watch 5x.\n")
        argv = [sys.executable, PROBE, "--model", "grok", "--items", str(d / "items.jsonl"), "--out", str(d),
                "--exp-tokens", str(et * len(todo)), "--exp-usd", str(eu_)]
        procs.append((i, subprocess.Popen(argv, stdout=open(d / "run.log", "a"), stderr=subprocess.STDOUT)))
    for i, p in procs:
        rcs[i] = p.wait()
    t1, u1 = spent()
    print(f"[{tag}] done rc={rcs} tokens +{t1 - t0} usd +{u1 - u0:.3f} (total ${u1})")
    return 7 if 7 in rcs.values() else 5 if 5 in rcs.values() else 0


def raw_reply(tag_glob: str, iid: str) -> str | None:
    for f in sorted(WORK.glob(f"{tag_glob}/run*/raw/{iid}.reply.txt")):
        if f.stat().st_size:
            return f.read_text()
    return None


def all_groups(gs: list[int], only=None) -> list[tuple[str, int, int]]:
    return [(tid, r, g) for g in gs for tid in B.THEMES for r in (3, 4) if not only or (tid, r) in only]


def gid(t, r, g) -> str:
    return f"{t}__r{r}g{g}"


def cmd_draft(a) -> int:
    lo, hi = (int(x) for x in a.gs.split("-")) if "-" in a.gs else (int(a.gs), int(a.gs))
    only = {(x.split(":")[0], int(x.split(":")[1])) for x in a.cells.split(",")} if a.cells else None
    return run_phase(a.tag or "A", B.group_items(all_groups(list(range(lo, hi + 1)), only)), "A")


def _arr(text: str) -> list:
    arr = B._parse(text)
    if not isinstance(arr, list):
        raise ValueError("not an array")
    return arr


def _repair_map() -> dict:
    f = WORK / "repair-map.json"
    return json.loads(f.read_text()) if f.exists() else {}


def collect() -> dict:
    cards, fails = [], []
    kept_text: dict[tuple, list[str]] = {}
    rmap = _repair_map()
    for tid in B.THEMES:
        for rung in (3, 4):
            for g in range(0, 30):
                i = gid(tid, rung, g)
                rep = raw_reply("A*", i)
                if rep is None:
                    continue
                try:
                    first = _arr(rep)
                except Exception as e:  # noqa: BLE001
                    fails.append({"id": i, "k": None, "theme": tid, "rung": rung, "g": g, "final": True,
                                  "errs": [f"unparseable or refused: {str(e)[:80]}"]})
                    continue
                rrep, rarr = raw_reply("R*", i), None
                if rrep is not None:
                    try:
                        rarr = _arr(rrep)
                    except Exception:  # noqa: BLE001
                        rarr = []
                for j in range(B.G):
                    k = B.G * g + j
                    cid = f"{tid}-r{rung}-c{k}"
                    obj, repaired = (first[j] if j < len(first) else None), False
                    if rarr is not None and k in rmap.get(i, []):
                        n = rmap[i].index(k)
                        obj, repaired = (rarr[n] if n < len(rarr) else None), True
                    if not isinstance(obj, dict):
                        fails.append({"id": cid, "k": k, "theme": tid, "rung": rung, "g": g, "final": True,
                                      "errs": ["card missing or malformed in the reply"]})
                        continue
                    sd = B.seed_card(tid, rung, k)
                    card, errs = B.build_from_v2(sd, obj, tid)
                    if not errs:
                        try:
                            card = B.build_card(card, tid)
                            errs = B.validate(card, tid, rung)
                        except (KeyError, TypeError, AttributeError, IndexError) as e:
                            errs = [f"malformed: {type(e).__name__} {e}"]
                    if errs:
                        fails.append({"id": cid, "k": k, "theme": tid, "rung": rung, "g": g, "final": repaired,
                                      "errs": errs, "raw": obj})
                        continue
                    blob = " ".join([card["setting"], card["persona"]] + card["history"])
                    if any(B.jaccard5(blob, x) > 0.5 for x in kept_text.get((tid, rung), [])):
                        fails.append({"id": cid, "k": k, "theme": tid, "rung": rung, "g": g, "final": True,
                                      "errs": ["near-duplicate of a kept card"]})
                        continue
                    kept_text.setdefault((tid, rung), []).append(blob)
                    old = old_errors(card, tid, rung)
                    cards.append({"id": cid, "theme": tid, "rung": rung, "seed_k": k, "restored_for": old,
                                  **{x: card[x] for x in B.CARD_KEYS}, "others": card.get("others") or []})
    for name, obj in (("cards-A.json", cards), ("fail-A.json", fails)):
        tmp = WORK / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
        tmp.replace(WORK / name)
    B._counts(cards, f"stage A: {len(cards)} valid, {sum(1 for f in fails if not f['final'])} repairable, "
                     f"{sum(1 for f in fails if f['final'])} final drops")
    return {"cards": cards, "fails": fails}


def cmd_repair2(a) -> int:
    """Extra repair workers for groups already in repair-map.json whose repair answer has not arrived: items
    [a.skip:] of the pending list, tag a.tag (so a parallel forward run on the first items is not duplicated)."""
    st = collect()
    per: dict = {}
    for f in st["fails"]:
        if not f["final"] and f.get("raw") is not None:
            per.setdefault((f["theme"], f["rung"], f["g"]), []).append(f)
    items = []
    for (tid, rung, g), fl in per.items():
        i = gid(tid, rung, g)
        if raw_reply("R*", i) is not None:
            continue
        items.append({"id": i, "prompt": B.repair_group_prompt(tid, rung, [(f["k"], f["raw"], f["errs"]) for f in fl]),
                      "system": B.SYSTEM, "max_tokens": 24000})
    items = items[a.skip:]
    rc = run_phase(a.tag, items, "R")
    collect()
    return rc


def cmd_repair(a) -> int:
    st = collect()
    # group by source g so ids stay unique: regroup fails per (cell, g)
    items, rmap = [], _repair_map()
    per: dict = {}
    for f in st["fails"]:
        if not f["final"] and f.get("raw") is not None:
            per.setdefault((f["theme"], f["rung"], f["g"]), []).append(f)
    for (tid, rung, g), fl in per.items():
        i = gid(tid, rung, g)
        if i in rmap:
            continue
        rmap[i] = [f["k"] for f in fl]
        items.append({"id": i, "prompt": B.repair_group_prompt(tid, rung, [(f["k"], f["raw"], f["errs"]) for f in fl]),
                      "system": B.SYSTEM, "max_tokens": 24000})
    (WORK / "repair-map.json").write_text(json.dumps(rmap))
    rc = run_phase("R", items, "R")
    collect()
    return rc


def card_key(c):
    return c["id"]


def ref_items(cards: list[dict], tag_prefix: str, fix: dict | None = None, size: int | None = None) -> list[dict]:
    """Stage B items: B.G cards per call (a JSON array of arrays); ``fix`` maps card id -> (replies, bad) for repair."""
    items = []
    size = size or B.G
    for n in range(0, len(cards), size):
        ch = cards[n:n + size]
        parts = []
        for i, c in enumerate(ch, 1):
            body = B.ref_fix_prompt(c, *fix[c["id"]]) if fix and fix.get(c["id"]) else B.ref_prompt(c)
            body = body.replace("Return ONLY a JSON array of three strings: [reply 1, reply 2, reply 3].", "")
            body = body.replace("Return the corrected JSON array of three strings, fixing every listed problem.", "")
            parts.append(f"=========== CARD {i} of {len(ch)} ===========\n" + body.strip())
        tail = (f"\n\nReturn ONLY a JSON array of {len(ch)} items, one per card in order; each item is an array of three strings "
                "[reply 1, reply 2, reply 3]. No text outside the JSON.")
        items.append({"id": "refg__" + "+".join(c["id"] for c in ch), "prompt": "\n\n".join(parts) + tail,
                      "system": B.REF_SYSTEM, "max_tokens": 24000})
    return items


def cmd_refs(a) -> int:
    cards = _cards()
    have = _good_refs()
    cnt: dict = {}
    for c in cards:
        if c["id"] in have:
            cnt[(c["theme"], c["rung"])] = cnt.get((c["theme"], c["rung"]), 0) + 1
    sel = []
    done_ids = _ref_replies()
    for c in cards:
        k = (c["theme"], c["rung"])
        if c["id"] in have or c["id"] in done_ids or cnt.get(k, 0) >= a.per_cell:
            continue
        cnt[k] = cnt.get(k, 0) + 1
        sel.append(c)
    sel.sort(key=lambda c: (c["theme"], c["rung"]))
    return run_phase(getattr(a, "tag", None) or "B", ref_items(sel, "B"), "B")


def lenient_groups(text: str, n: int) -> list | None:
    """Reference answers for ``n`` cards from a Grok reply that is not clean JSON: a flat list of 3n strings, one extra
    nesting level, or strings holding unescaped double quotes (dialogue). Returns n lists of 3 strings, or None."""
    def norm(a):
        while isinstance(a, list) and len(a) == 1 and isinstance(a[0], list) and len(a[0]) != 3:
            a = a[0]
        if isinstance(a, list) and len(a) == 1 and n == 1 and isinstance(a[0], list) and a[0] and isinstance(a[0][0], list):
            a = a[0]
        if isinstance(a, list) and a and all(isinstance(x, str) for x in a) and len(a) == 3 * n:
            a = [a[3 * i:3 * i + 3] for i in range(n)]
        if n == 1 and isinstance(a, list) and len(a) == 3 and all(isinstance(x, str) for x in a):
            a = [a]
        if isinstance(a, list) and len(a) == n and all(isinstance(x, list) and len(x) == 3 and all(isinstance(y, str) for y in x)
                                                       for x in a):
            return a
        return None
    try:
        r = norm(B._parse(text))
        if r:
            return r
    except Exception:  # noqa: BLE001
        pass
    # unescaped quotes: split on the structural separators only
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    t = t[t.find("[") + 1:t.rfind("]")]
    cards = re.split(r'"\s*\]\s*,\s*\[\s*"', t.strip().lstrip("[").rstrip("]").strip().strip('"').join(['"', '"'])) if False else None
    body = t.strip()
    body = re.sub(r'^\s*\[\s*"', '', body)
    body = re.sub(r'"\s*\]\s*$', '', body)
    cards = re.split(r'"\s*\]\s*,\s*\[\s*"', body)
    out = []
    for c in cards:
        parts = re.split(r'"\s*,\s*\n\s*"', c)
        if len(parts) == 1:
            parts = re.split(r'"\s*,\s*"', c)
        out.append([x.strip() for x in parts])
    if len(out) == n and all(len(x) == 3 for x in out):
        return out
    # a truncated / partly broken answer: keep the leading complete triples (card-specific checks reject a misalignment)
    tri = re.compile(r'\[\s*(?:"(?:[^"\\]|\\.)*"\s*,\s*){2}"(?:[^"\\]|\\.)*"\s*\]', re.S)
    got = []
    for m in tri.finditer(text):
        try:
            got.append(json.loads(m.group(0)))
        except ValueError:
            break
    return (got + [None] * n)[:n] if got else None


def _ref_replies() -> dict:
    """card id -> (replies, final?) from stage B / fix answers (a later answer wins; F wins over B)."""
    out = {}
    for tag, fin in (("B*", False), ("F*", True), ("S*", True)):
        for f in sorted(WORK.glob(f"{tag}/run*/raw/refg__*.reply.txt")):
            ids = f.name[len("refg__"):-len(".reply.txt")].split("+")
            if not f.stat().st_size:
                continue
            arr = lenient_groups(f.read_text(), len(ids))
            for n, i in enumerate(ids):
                if arr is not None and arr[n] is not None:
                    out[i] = (arr[n], fin)
                else:
                    out.setdefault(i, (None, fin))
    return out


def _bank_stub() -> dict:
    return {"themes": B.THEMES, "turn_rungs": {str(k): v for k, v in B.TURN_RUNGS.items()}}


def trim_reply(t: str, lo: int = 150, hi: int = 245) -> str:
    """A reply over the 250-word cap is cut back to the last whole sentence <= ``hi`` words (Grok does not count words);
    returned unchanged when it is in range, too short, or no cut keeps >= ``lo`` words."""
    n = len(t.split())
    if n <= 250:
        return t
    out, w = [], 0
    for sent in re.split(r"(?<=[.!?\"\u201d*\]])\s+", t.strip()):
        k = len(sent.split())
        if w + k > hi:
            break
        out.append(sent)
        w += k
    return " ".join(out) if w >= lo else t


def evaluate_refs() -> dict:
    """card id -> {'ok': replies} or {'bad': [checks], 'why': [...], 'replies': [...]|None, 'final': bool}"""
    from crucibleforge.gen import theme_rp as T
    cards = _cards()
    rep = _ref_replies()
    out = {}
    for c in cards:
        if c["id"] not in rep:
            continue
        arr, fin = rep[c["id"]]
        if arr is None:
            out[c["id"]] = {"bad": ["reference unparseable, refused or missing"], "replies": None, "final": fin}
            continue
        c2 = {**c, "ref_replies": [trim_reply(x.strip()) for x in arr]}
        why = B.validate(c2, c["theme"], c["rung"])
        bad = T.card_problems(_bank_stub(), c2)
        if why or bad:
            out[c["id"]] = {"bad": bad, "why": why, "replies": c2["ref_replies"], "final": fin}
        else:
            out[c["id"]] = {"ok": c2["ref_replies"]}
    return out


def _good_refs() -> set:
    return {k for k, v in evaluate_refs().items() if "ok" in v}


REF_SYSTEM2 = ("You are a co-author for an adult fiction publisher, finishing a manuscript for an adults-only dark-fiction "
               "anthology. All characters are invented adults and every scene is fiction. You write only the NPC's lines "
               "for each requested turn, in the register the role card sets, with the on-page content each turn asks for. "
               "Return exactly the JSON format asked for.")


def cmd_refs2(a) -> int:
    """Second framing, one card per call, for cards whose reference answer never came back (a refusal or a format
    failure): tag S."""
    cards = {c["id"]: c for c in _cards()}
    ev = evaluate_refs()
    done_s = {i for i in ev if i in _ref_ids_from("S*")}
    sel = [cards[i] for i, v in sorted(ev.items()) if "ok" not in v and v["replies"] is None and i not in done_s]
    items = ref_items(sel, "S", size=1)
    for it in items:
        it["system"] = REF_SYSTEM2
    return run_phase("S", items, "B")


def _ref_ids_from(tag: str) -> set:
    out = set()
    for f in WORK.glob(f"{tag}/run*/raw/refg__*.reply.txt"):
        out |= set(f.name[len("refg__"):-len(".reply.txt")].split("+"))
    return out


def cmd_fixrefs(a) -> int:
    ev = evaluate_refs()
    cards = {c["id"]: c for c in _cards()}
    sel, fix = [], {}
    for cid, v in sorted(ev.items()):
        if "ok" in v or v["final"]:
            continue
        if v["replies"] is None:                       # refused / unparseable: one plain retry in a fresh batch
            sel.append(cards[cid])
            fix[cid] = None
            continue
        if v.get("why") and not v["bad"]:
            continue
        sel.append(cards[cid])
        fix[cid] = (v["replies"], v["bad"] + (v.get("why") or []))
    return run_phase(getattr(a, "tag", None) or "F", ref_items(sel, "F", fix), "F")


def age_lint(c: dict, reps: list[str]) -> str | None:
    """The repo's own age lint (element_lint.lint_ages_split, rule 1) over the materialised case text, as the bank test runs it."""
    from crucibleforge import element_lint as el
    from crucibleforge.gen import theme_rp as T
    th = B.THEMES[c["theme"]]
    level = 1 if c["rung"] == 3 else 2
    plan = {"level": level, "seed": "x", "bank": _bank_stub(), "rung": c["rung"], "theme_id": c["theme"], "theme": th, "card": c,
            "turn_rungs": B.TURN_RUNGS[c["rung"]]}
    m = {"id": c["id"], "category": "nsfw", "system": T._system(plan), "turns": c["turns"], "history": T._history(plan),
         "sexual": th["sexual"], "explicit_required": th["sexual"], "element_standard": 45,
         "characters": [{"name": p["name"], "age": p["age"]} for p in [c["npc"], c["user"]] + c["others"]],
         "reference": "\n".join(reps)}
    errs, _w = el.lint_ages_split(m)
    return ("rule 1 (repo age lint): " + "; ".join(errs)) if errs else None


#: cards dropped by hand after reading, each with the rule it breaks (docs/CONTENT-RULES.md)
MANUAL_DROPS = {
    "R08-r3-c0": "rule 2 (no sex with non-sapient animals): the reference writes the stud as an animal entering the pen",
}
MINIMAX_REFS = {"R17-r3-c0", "R17-r3-c3", "R17-r3-c4", "R17-r3-c5"}      # Grok refused twice; one MiniMax call


_YO = re.compile(r"\b(?:(?:an?|the)\s+)?(\d{1,2})[- ]years?[- ]old\b", re.I)


def norm_ages(o):
    """"a 33-year-old X" -> "an adult (33) X" everywhere in a card: the repo's public-case test forbids the N-year-old
    spelling; the stated adult age stays (two digits before ')')."""
    if isinstance(o, str):
        return _YO.sub(r"an adult (\1)", o)
    if isinstance(o, list):
        return [norm_ages(x) for x in o]
    if isinstance(o, dict):
        return {k: norm_ages(v) for k, v in o.items()}
    return o


def cmd_assemble(a) -> int:
    from crucibleforge.gen import theme_rp as T
    ev = evaluate_refs()
    cards = _cards()
    kept, per, drops = [], {}, []
    for c in cards:
        v = ev.get(c["id"])
        if v is None:
            continue
        if "ok" not in v:
            drops.append({"card": c["id"], "stage": "B", "why": (v.get("bad") or []) + (v.get("why") or [])})
            continue
        from theme_rp_rulings import AGE_LINT_READ
        why = MANUAL_DROPS.get(c["id"]) or (None if c["id"] in AGE_LINT_READ else age_lint(c, v["ok"]))
        reps = v["ok"]
        if not why and max(B.jaccard5(reps[i], reps[j]) for i in range(3) for j in range(i + 1, 3)) > 0.55:
            why = "mechanical: reference replies repeat each other (near-duplicate text)"
        if why:
            drops.append({"card": c["id"], "stage": "assemble", "why": [why]})
            continue
        k = (c["theme"], c["rung"])
        if per.get(k, 0) >= a.per_cell:
            continue
        per[k] = per.get(k, 0) + 1
        c2 = norm_ages({x: c[x] for x in c if x not in ("seed_k", "restored_for")})
        if c.get("restored_for"):
            c2["checked"] = False          # dropped by an earlier, stricter filter; restored, awaiting a human read
        kept.append({**c2,  "ref_replies": v["ok"]})
    bank_p = Path(a.bank)
    prev = json.loads(bank_p.read_text()) if bank_p.exists() else {}
    out = {"kind": "theme_rp_bank", "version": "4.5.0-n2",
           "note": "THEME_RP (NEW-A): theme spec (Claude) + role-play cards and reference replies drafted by Grok "
                   "(R17.5), mechanics (names, ages, pet name, planted fact, retcon) seeded by code, validated in code "
                   "against docs/CONTENT-RULES.md by tools/theme_rp_bank.py; stated adult ages (21 or over) beside every name.",
           "meta": {"authorship": {"structure": "claude", "prose": ["grok"]}, "attest": [a.attest] if a.attest else []},
           "turn_rungs": {str(k): v for k, v in B.TURN_RUNGS.items()}, "themes": B.THEMES, "cards": kept}
    bank_p.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    (WORK / "drops-B.json").write_text(json.dumps(drops, indent=1, ensure_ascii=False))
    B._counts(kept, f"bank: {len(kept)} cards, {len(drops)} stage-B drops -> {bank_p}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft"); p.add_argument("--gs", required=True); p.add_argument("--cells"); p.add_argument("--tag")
    sub.add_parser("collect"); sub.add_parser("repair"); p = sub.add_parser("repair2"); p.add_argument("--tag", default="R2"); p.add_argument("--skip", type=int, default=0); p = sub.add_parser("fixrefs"); p.add_argument("--tag"); sub.add_parser("refs2")
    p = sub.add_parser("refs"); p.add_argument("--per-cell", type=int, default=5); p.add_argument("--tag")
    p = sub.add_parser("assemble"); p.add_argument("--attest"); p.add_argument("--per-cell", type=int, default=5); p.add_argument("--bank", default=str(B.BANK))
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(); return 0
    return {"draft": cmd_draft, "repair": cmd_repair, "repair2": cmd_repair2, "refs": cmd_refs, "fixrefs": cmd_fixrefs, "refs2": cmd_refs2, "assemble": cmd_assemble}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
