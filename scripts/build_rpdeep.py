#!/usr/bin/env python3
"""Compile cases/rp-deep/<id>/{card.md,history.md,session.yaml} into cases/rpdeep.json.

The markdown/yaml files are the human-readable source; the JSON is what the
runner reads (and what version.py hashes, so the full card text is covered).
Validation (fails the build):
  - prior log strictly alternates user/assistant, starts with user, ends with assistant
  - every element's evidence text occurs verbatim (whitespace-normalised) in its source
  - element/check turn numbers are in range; an element grades at most 2 turns
  - scored fact ids are unique (one grader per fact)
  - no two different scored facts take evidence from the same card paragraph (warning)
Usage: python scripts/build_rpdeep.py [--check]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "cases" / "rp-deep"
OUT = ROOT / "cases" / "rpdeep.json"
MAX_GROUP_TURNS = 2


def norm(s: str) -> str:
    return " ".join(s.split())


def parse_history(text: str) -> list[dict]:
    parts = re.split(r"^### (USER|ASSISTANT)[ \t]*\n", text, flags=re.M)
    if parts[0].strip():
        raise SystemExit("history.md: text before the first ### marker")
    msgs = [{"role": r.lower(), "content": c.strip()} for r, c in zip(parts[1::2], parts[2::2])]
    for i, m in enumerate(msgs):
        want = "user" if i % 2 == 0 else "assistant"
        if m["role"] != want:
            raise SystemExit(f"history.md: message {i + 1} is {m['role']}, expected {want} "
                             "(the log must strictly alternate, starting with the user)")
        if not m["content"]:
            raise SystemExit(f"history.md: message {i + 1} is empty")
    if not msgs or msgs[-1]["role"] != "assistant":
        raise SystemExit("history.md: the log must end on an assistant message")
    return msgs


#: Keys a hand-assembled 4.5 case carries that session.yaml does not: they are
#: kept from the existing cases/rpdeep.json on a rebuild (E6).
STAMP_STANDARD = 45


#: Row keys only a hand pass sets (element metadata, retire records): kept from
#: the existing JSON row. Every other key is source-owned (session.yaml).
META_KEYS = ("class", "evidence_type", "anchor", "gold_quote", "quote_scope", "weight",
             "scored", "status", "pruned", "unpruned", "replaces", "veto", "note")


def _merge_rows(src: list[dict], old: list[dict]) -> list[dict]:
    """Rows keyed by id: the source owns every key except the metadata
    (META_KEYS), which an existing row keeps; an old row the source no longer
    lists (a retire record, ``status: retire``) is kept as is. Order = the old
    rows' order, then new source rows."""
    by_src = {r["id"]: r for r in src if isinstance(r, dict) and "id" in r}
    out, seen = [], set()
    for r in old:
        s = by_src.get(r.get("id"))
        if s is None:
            out.append(r)
        else:
            row = dict(s)
            row.update({k: r[k] for k in META_KEYS if k in r})
            out.append(row)
        seen.add(r.get("id"))
    out += [r for r in src if not (isinstance(r, dict) and r.get("id") in seen)]
    return out


def merge_existing(case: dict, old: dict | None) -> dict:
    """Overlay the existing JSON case: the source owns every key it builds
    (q, pattern, temperature ...), the old JSON only adds keys the source does
    not build (hand-added stamps), and a 45.x case is stamped
    ``element_standard: 45``."""
    if old:
        merged = {**old, **case}
        for k in ("judge_elements", "checks"):
            if k in case:
                merged[k] = _merge_rows(case[k], old.get(k) or [])
        case = merged
    if str(case.get("case_revision", "")).startswith("45") and "element_standard" not in case:
        case["element_standard"] = STAMP_STANDARD
    return case


def build_case(d: Path, old: dict | None = None) -> tuple[dict, list[str]]:
    warns: list[str] = []
    card = (d / "card.md").read_text(encoding="utf-8").strip()
    # a short session may start cold: no earlier conversation (3.9 step 6)
    hp = d / "history.md"
    history = parse_history(hp.read_text(encoding="utf-8")) if hp.exists() else []
    s = yaml.safe_load((d / "session.yaml").read_text(encoding="utf-8"))
    n = len(s["turns"])
    # evidence sources: the card, the earlier conversation, or (3.9 step 6) the
    # scripted live USER turns — a fact the user tells the companion on turn 2
    # that she must remember on turn 7
    srcs = {"card": norm(card), "log": norm(" ".join(m["content"] for m in history)),
            "turn": norm(" ".join(s["turns"]))}
    paras = [norm(p) for p in re.split(r"\n\s*\n", card)]
    facts: dict[str, str] = {}
    para_of: dict[int, set] = {}
    for c in s.get("checks") or []:
        for t in c.get("turns") or []:
            if not 1 <= t <= n:
                raise SystemExit(f"{d.name}: check {c['id']} turn {t} out of range")
        if c.get("fact"):
            if not c.get("ftype"):
                raise SystemExit(f"{d.name}: check {c['id']} has a fact but no ftype")
            if c["fact"] in facts:
                raise SystemExit(f"{d.name}: fact {c['fact']} graded twice")
            facts[c["fact"]] = c["id"]
    for e in s.get("judge_elements") or []:
        ts = e.get("turns") or []
        if not ts or len(ts) > MAX_GROUP_TURNS or any(not 1 <= t <= n for t in ts):
            raise SystemExit(f"{d.name}: element {e['id']} turns {ts} invalid")
        fid = e.get("fact") or e["id"].split("-")[0]
        if fid in facts:
            raise SystemExit(f"{d.name}: fact {fid} graded twice")
        facts[fid] = e["id"]
        e["fact"] = fid
        for ev in e.get("evidence") or []:
            if norm(ev["text"]) not in srcs[ev["src"]]:
                raise SystemExit(f"{d.name}: element {e['id']} evidence not found verbatim in "
                                 f"{ev['src']}: {ev['text'][:80]!r}")
            if ev["src"] == "card":
                for i, p in enumerate(paras):
                    if norm(ev["text"]) in p:
                        # the halves of a split item (I1a/I1b) and the phases of one
                        # secret (X1-X3) are one planted fact
                        base = "X" if re.fullmatch(r"X\d", fid) else re.sub(r"[ab]$", "", fid)
                        para_of.setdefault(i, set()).add(base)
    for i, fs in para_of.items():
        if len(fs) > 1:
            warns.append(f"{d.name}: card paragraph {i} is evidence for several facts: {sorted(fs)}")
    system = s["rules"].strip() + "\n\n" + card
    case = {
        "id": s["id"],
        "system": system,
        "history": history,
        "turns": [t.strip() for t in s["turns"]],
        "temperature": s.get("temperature", 0.8),
        "top_p": s.get("top_p", 0.95),
        "max_tokens": s.get("max_tokens", 2000),
        "rubric": "rp_deep",
        "judge_key": s["judge_key"].strip(),
        "judge_card_summary": s["judge_card_summary"].strip(),
        "user_name": s.get("user_name"),
        "min_context": s.get("min_context"),
        "checks": s.get("checks") or [],
        "judge_elements": s.get("judge_elements") or [],
        # 3.9 step 6: "chat" = a companion talking to the user (no dialogue
        # requirement in the format gate); deep_rubric picks the fact-group
        # rubric (judge.DEEP_GROUP_RUBRICS)
        "format_gate": s.get("format_gate"),
        "deep_rubric": s.get("deep_rubric"),
    }
    if s.get("smoke"):
        case["smoke"] = True
    for k in ("case_revision", "element_standard", "meta", "note", "revision_note",
              "review_status"):
        if s.get(k) is not None:
            case[k] = s[k]
    case = {k: v for k, v in case.items() if v is not None}
    return merge_existing(case, old), warns


LADDER = ROOT / "cases" / "ladder"


def _pack_files() -> list[Path]:
    """Every pack file: ``set-<n>.json`` (hand-written or generated) and the 4.5
    classic-fixed ``classic-<name>.json`` packs."""
    return sorted(set(LADDER.rglob("set-*.json")) | set(LADDER.rglob("classic-*.json")))


def _ladder_sets() -> dict[str, Path]:
    """{case id: set file} for every rp-deep session a ladder pack lists as
    an object.
    Those sessions are written INLINE into their set file (ladder.inline_cases),
    not into rpdeep.json: rpdeep.json is part of the suite hash, so a new pack
    must never touch it (that would mark every existing row stale)."""
    out = {}
    for f in _pack_files():
        # 4.5: a generated set holds ``generator`` and no ``cases`` (it expands at load time),
        # and a classic-fixed pack may list plain ids only: neither inlines a session
        for c in json.loads(f.read_text(encoding="utf-8")).get("cases") or []:
            # only entries written as objects ({"id": ...}) are inlined; a
            # plain id string keeps that session in rpdeep.json (3.9 packs)
            if isinstance(c, dict) and (SRC / c["id"] / "session.yaml").exists():
                out[c["id"]] = f
    return out


def _retired_pack_sessions(listed: dict[str, Path]) -> set[str]:
    """Session dirs of 4.1 ladder-pack questions (written inline in the 4.1.0 pack files) that no
    live pack lists any more: 4.5 replaced those packs with generated sets, the session sources
    stay under cases/rp-deep/ for the archive. They belong in no JSON (rpdeep.json is hashed into
    the suite; a retired pack question must not move it), so the build leaves them out."""
    arch = ROOT / "archive" / "4.1.0" / "cases" / "ladder"
    ids: set[str] = set()
    for f in sorted(arch.rglob("set-*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")).get("cases") or []:
            if isinstance(c, dict) and (SRC / c["id"] / "session.yaml").exists():
                ids.add(c["id"])
    return ids - set(listed)


def main() -> int:
    cases, warns = [], []
    try:
        existing = {c["id"]: c for c in json.loads(OUT.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        existing = {}
    in_pack = _ladder_sets()
    retired = _retired_pack_sessions(in_pack)
    pack_cases: dict[str, dict] = {}
    for d in sorted(p for p in SRC.iterdir() if p.is_dir() and (p / "session.yaml").exists()):
        if d.name in retired:
            print(f"{d.name}: retired 4.1 pack session (no live pack lists it), not built")
            continue
        c, w = build_case(d, existing.get(d.name))
        if d.name in in_pack:
            pack_cases[d.name] = dict(c, category="rpdeep")
        else:
            cases.append(c)
        warns += w
        words = len(d.joinpath("card.md").read_text(encoding="utf-8").split())
        logw = sum(len(m["content"].split()) for m in c["history"])
        scored = len({x.get("fact") for x in c["checks"] if x.get("fact")}) + len(c["judge_elements"])
        print(f"{c['id']}: card {words} words, log {len(c['history'])} msgs/{logw} words, "
              f"{len(c['turns'])} turns, {scored} scored facts")
    for w in warns:
        print("WARN", w)
    text = json.dumps(cases, ensure_ascii=False, indent=1) + "\n"
    sets = {}
    for f in sorted(set(in_pack.values())):
        spec = json.loads(f.read_text(encoding="utf-8"))
        spec["cases"] = [pack_cases.get(c["id"] if isinstance(c, dict) else c, c) for c in spec["cases"]]
        sets[f] = json.dumps(spec, indent=1, ensure_ascii=True) + "\n"
    if "--check" in sys.argv:
        stale = not OUT.exists() or OUT.read_text(encoding="utf-8") != text
        stale |= any(f.read_text(encoding="utf-8") != t for f, t in sets.items())
        return 1 if stale else 0
    OUT.write_text(text, encoding="utf-8")
    for f, t in sets.items():
        f.write_text(t, encoding="utf-8")
        print(f"wrote {f.relative_to(ROOT)}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
