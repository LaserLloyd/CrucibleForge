"""Build cases/judged/rpsession-ladder.json: the frozen ladders of the "Role-play: sessions" part (crucibleforge/rpladder).

    CF_COMPOSITION=rc4-main uv run python tools/build_rpsession_ladder.py \
        --folder grok=<results dir> --folder ref=<results dir> --folder dsflash=<results dir> \
        --folder mm=<results dir> [--out cases/judged/rpsession-ladder.json] [--check]

Rows: ONLY the rows the board counts as current (report.board_filter under the shipping composition, newest row per
unit); a row whose user turns or system text differ from what the generator materialises today is refused. Per card
and rung model: the cleanest writer seed by the row's stored code checks (no failed check whose id starts with
"voice", "nopuppet" or "empty-turn"; then the fewest failed checks; then the lower seed). Each rung stores its
replies, the user turns it answered, its source row and its failed checks next to its sha. R4 = the card's
handwritten reference from the bank.

Ladders (lead 2026-11-15): every card ``standard`` = R1 Grok < R2 reference 27B < R3 DeepSeek flash; RPS2-greenhouse
may carry alternatives in CARD_LADDERS (none today; the rung check picks the candidate with the most usable axes;
none usable -> "not judged on this card"). Rung models are publishable models only, never Claude Code rows.
``--check`` rebuilds in memory and fails when the file on disk differs.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LADDERS = {
    "standard": {1: "grok", 2: "ref", 3: "dsflash"},
}
#: per-card alternative ladders (the rung check takes the candidate with the most usable axes). None today: the RPS2
#: alternative (reference 27B < MiniMax < DS flash) had no clean current MiniMax seed (both break voice-cap), and the
#: lead ruled 2026-11-15: no alternative, the card keeps its standard ladder or reads "not judged on this card"
CARD_LADDERS: dict = {}
EXCLUDE_IDS = ("voice", "nopuppet", "empty-turn")
#: decisions about the frozen selection (recorded, not computed)
SELECTION_NOTES = [
    "2026-11-15 (lead): RPS1-harbour R1 (Grok s2) and RPS4-ferry R2 (ref s2) were chosen by a check the scrub later "
    "found wrong (Grok s2 know@9 accidental pass; ref s1 canon2@8 false negative); with the checks corrected each "
    "is a tie broken by seed number, which carries no quality claim, so the rungs are kept."]
#: never a rung (validation-only rows)
REFUSED_MODELS = ("claude", "cc-")


def current_units(folder: str) -> tuple[str, dict]:
    from crucibleforge import config, report, chains
    fs = glob.glob(os.path.join(folder, "transcripts_*.jsonl"))
    if len(fs) != 1:
        raise SystemExit(f"{folder}: expected one transcripts file, found {len(fs)}")
    label = os.path.basename(fs[0])[len("transcripts_"):-len(".jsonl")]
    raw = [json.loads(l) for l in open(fs[0], encoding="utf-8") if l.strip()]
    cfg = {"_profile": "bench45", "models": []}
    board = report.board_filter(label, cfg, rows=config.dedupe_rows(copy.deepcopy(raw)))
    best: dict[str, dict] = {}
    for r in board:
        ch = r.get("chain") or {}
        if r.get("rubric") != "rp_long" or ch.get("part") != "chat/j_rpsession":
            continue
        if ch["id"] not in best or str(r.get("ts")) >= str(best[ch["id"]].get("ts")):
            best[ch["id"]] = r
    return label, best


def script() -> dict:
    from crucibleforge import chains, profiles
    profiles.set_active("bench45")
    prof = profiles.load_profile("bench45")
    profiles.activate_cases_root(prof)
    out = {}
    for c in chains.chain_cases(prof):
        sp = c.get("chain_spec") or {}
        if sp.get("part") != "chat/j_rpsession" and not c["id"].startswith("FLAT-chatrp_pilot"):
            continue
        m = chains.materialize(chains.instance(c, 1, 1))
        out[c["id"]] = {"system": m["system"], "turns": list(m["turns"]), "card": m["meta"]["card"]}
    return out


def build(folders: dict[str, str]) -> dict:
    from crucibleforge import rpladder as RL
    from crucibleforge.gen import rpsession as G
    if os.environ.get("CF_COMPOSITION", "") != "rc4-main":
        raise SystemExit("run with CF_COMPOSITION=rc4-main (the shipping composition's board filter)")
    sc = script()
    bank = {c["id"]: c for c in G.load_bank()["cards"]}
    units: dict[str, dict] = {}      # model key -> {card: [(seed, row)]}
    labels = {}
    for key, folder in folders.items():
        label, best = current_units(folder)
        if any(x in label.lower() for x in REFUSED_MODELS):
            raise SystemExit(f"{label}: Claude Code rows are validation-only, never a rung")
        labels[key] = label
        for uid, r in best.items():
            s = sc.get(uid)
            if s is None:
                raise SystemExit(f"{label} {uid}: not a unit of the shipping composition")
            conv = r["conversation"]
            users = [x["content"] for x in conv if x["role"] == "user"]
            if conv[0]["content"].strip() != s["system"].strip() or users != s["turns"]:
                raise SystemExit(f"{label} {uid}: refused: its user turns are not today's script (not a current row)")
            units.setdefault(key, {}).setdefault(s["card"], []).append((int(r["chain"].get("gen_seed") or 0), r))
    cards = {}
    for cid in sorted(bank):
        c = bank[cid]
        sys_t = next(s["system"] for s in sc.values() if s["card"] == cid)
        turns = next(s["turns"] for s in sc.values() if s["card"] == cid)
        if sys_t.strip() != c["system"].strip() or turns != list(c["turns"]):
            raise SystemExit(f"{cid}: the generator's script and the bank differ")
        cands = {"standard": LADDERS["standard"], **CARD_LADDERS.get(cid, {})}
        entry = {"system": sys_t, "turns": turns, "candidates": {}, "order": []}
        for name, spec in cands.items():
            rungs = {}
            why = None
            for k, key in spec.items():
                cand = []
                for seed, r in units.get(key, {}).get(cid, []):
                    fails = [x["id"] for x in (r.get("checks") or {}).get("results") or [] if not x.get("pass")]
                    bad = [x for x in fails if x.startswith(EXCLUDE_IDS)]
                    cand.append((bool(bad), len(fails), seed, fails, r))
                if not cand:
                    raise SystemExit(f"{cid}/{name} R{k}: no current {key} row on this card")
                cand.sort(key=lambda t: t[:3])
                bad, _, seed, fails, r = cand[0]
                if bad:
                    why = (f"R{k}: no clean {key} seed (voice / nopuppet / empty-turn): "
                           + "; ".join(f"s{t[2]} fails {', '.join(t[3])}" for t in cand))
                    if name == "standard":
                        raise SystemExit(f"{cid}/{name} {why}")
                    break
                conv = r["conversation"]
                reps = [x.get("content") or "" for x in conv if x["role"] == "assistant"]
                rungs[str(k)] = {"replies": reps, "users": [x["content"] for x in conv if x["role"] == "user"],
                                 "sha": RL.anchor_sha(reps), "fails": fails,
                                 "source": {"key": key, "label": labels[key], "model_id": r.get("model_id"),
                                            "unit": r["chain"]["id"], "seed": seed, "case_id": r.get("case_id"),
                                            "ts": r.get("ts"), "chain_revision": r.get("chain_revision")}}
            if why:
                # an alternative ladder that cannot be built from clean current rows is left out, with the reason
                entry.setdefault("not_built", {})[name] = why
                print(f"{cid}/{name}: not built: {why}")
                continue
            ref = list(c["reference"])
            rungs["4"] = {"replies": ref, "sha": RL.anchor_sha(ref), "fails": [],
                          "source": {"key": "reference", "bank_sha": G.BANK_SHA}}
            lad = {"rungs": rungs}
            lad["ladder_sha"] = RL.ladder_sha_of(lad)
            entry["candidates"][name] = lad
            entry["order"].append(name)
        cards[cid] = entry
    return {"note": "Frozen ladders for Role-play: sessions (crucibleforge/rpladder). Built by "
                    "tools/build_rpsession_ladder.py from rows the board counts as current; never hand-edit "
                    "(every sha is verified on load).", "selection_notes": SELECTION_NOTES, "bank_sha": G.BANK_SHA,
            "cards": cards}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", action="append", default=[], help="key=results dir (keys: grok, ref, dsflash, mm)")
    ap.add_argument("--out", default=str(ROOT / "cases" / "judged" / "rpsession-ladder.json"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    folders = dict(x.split("=", 1) for x in a.folder)
    d = build(folders)
    text = json.dumps(d, indent=1, ensure_ascii=False) + "\n"
    if a.check:
        same = Path(a.out).read_text(encoding="utf-8") == text
        print("ladder file current" if same else "ladder file DIFFERS from a rebuild")
        raise SystemExit(0 if same else 1)
    Path(a.out).write_text(text, encoding="utf-8")
    for cid, c in d["cards"].items():
        for name, lad in c["candidates"].items():
            print(cid, name, lad["ladder_sha"], " ".join(
                f"R{k}={r['source'].get('key')}:s{r['source'].get('seed', '-')}[{len(r['fails'])} fails]"
                for k, r in lad["rungs"].items()))


if __name__ == "__main__":
    main()
