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


def build_case(d: Path) -> tuple[dict, list[str]]:
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
    case = {k: v for k, v in case.items() if v is not None}
    return case, warns


LADDER = ROOT / "cases" / "ladder"


def _ladder_sets() -> dict[str, Path]:
    """{case id: set file} for every rp-deep session a ladder pack lists as
    an object.
    Those sessions are written INLINE into their set file (ladder.inline_cases),
    not into rpdeep.json: rpdeep.json is part of the suite hash, so a new pack
    must never touch it (that would mark every existing row stale)."""
    out = {}
    for f in sorted(LADDER.rglob("set-*.json")):
        for c in json.loads(f.read_text(encoding="utf-8"))["cases"]:
            # only entries written as objects ({"id": ...}) are inlined; a
            # plain id string keeps that session in rpdeep.json (3.9 packs)
            if isinstance(c, dict) and (SRC / c["id"] / "session.yaml").exists():
                out[c["id"]] = f
    return out


def main() -> int:
    cases, warns = [], []
    in_pack = _ladder_sets()
    pack_cases: dict[str, dict] = {}
    for d in sorted(p for p in SRC.iterdir() if p.is_dir() and (p / "session.yaml").exists()):
        c, w = build_case(d)
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
