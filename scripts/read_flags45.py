#!/usr/bin/env python3
"""Read the U2 opinion flags (refused, position) for stored replies, or take mark C7b (the constructed flag set).

  uv run python scripts/read_flags45.py --results <dir> --cap-usd N [--readers role,...] [--model LABEL]
        [--profile P] [--c1 marks45.json] [--config models.yaml] [--workers K] [--limit N] [--dry-run]
  uv run python scripts/read_flags45.py --results <dir> --constructed --cap-usd N [--readers role,...] [...]

Reading: every stored U2 unit of each ``transcripts_<label>.jsonl`` is shown to the first two admitted readers
(roles 0 and 1) as the card's own question plus the reply between delimiter lines; one flag line per (unit, reader)
goes to ``<results>/readers45/<label>.jsonl`` (resumable: an identical stamp is never asked again). A reader reads
flags only when it is a C1-admitted classing reader for that row AND holds a stored C7b pass for its exact model
id + prompt sha (marks45.json classing.C7_constructed.per_reader); without both it makes no call.
--constructed runs the constructed set per candidate reader and stores the result there.

Exit 0 done, 2 no admitted readers / nothing to read, 3 spend cap reached (resume to continue)."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from crucibleforge import classing45 as C, config, flags45 as F, lineage, nsfwplus45 as N  # noqa: E402
from crucibleforge.judge45 import HostedGrader  # noqa: E402
import read45  # noqa: E402


def _cost(reader, usage) -> float:
    p = reader.price
    if not (p and usage):
        return 0.0
    return ((usage.get("in") or 0) * p["input"] + (usage.get("out") or 0) * p["output"]) / 1e6


def take_c7b(res: Path, profile: dict, prompt: tuple, cap_usd, spent: list) -> dict:
    """Run the constructed set for every candidate reader; merge the results into marks45.json."""
    mp = res / "marks45.json"
    cur = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else {}
    per = {}
    for r in C.candidates(profile):
        if r.hosted and (not r.price or cap_usd is None):
            per[r.name] = {"model": r.model_id, "status": "not_measured", "why": "hosted reader needs a price and --cap-usd"}
            continue
        per[r.name] = F.constructed_for(
            r, prompt, res / "readers45" / f"constructed-flags-{r.name}.jsonl",
            cap_ok=lambda: cap_usd is None or spent[0] < cap_usd,
            on_usage=lambda u, r=r: spent.__setitem__(0, spent[0] + _cost(r, u)))
        print(f"C7b {r.name}: {per[r.name]['status']} {per[r.name].get('correct', '')} misses {per[r.name].get('misses', '')}",
              flush=True)
    blk = (cur.get("classing") or {})
    prev = (blk.get("C7_constructed") or {}).get("per_reader") or {}
    per = {**prev, **per}
    st = [v["status"] for v in per.values()]
    blk["C7_constructed"] = {"status": "pass" if "pass" in st else "fail" if "fail" in st else "not_measured",
                             "per_reader": per, "flag_prompt_sha": prompt[1]}
    cur["classing"] = blk
    res.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return per


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--constructed", action="store_true")
    ap.add_argument("--readers")
    ap.add_argument("--cap-usd", type=float)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--profile")
    ap.add_argument("--c1", help="marks45.json with the stored C1 and C7b results (default: <results>/marks45.json)")
    ap.add_argument("--config")
    ap.add_argument("--model")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = Path(a.results)
    profile = read45.load_profile(a.profile)
    if a.readers:
        profile = read45.restrict(profile, {x.strip().lower() for x in a.readers.split(",")})
    C.set_config(config.load_config(a.config) if a.config else None)
    fprompt = F.load_prompt()
    if a.constructed:
        spent = [0.0]
        per = take_c7b(res, profile, fprompt, a.cap_usd, spent)
        print(f"C7b hosted spend ${spent[0]:.4f}")
        return 0 if any(v["status"] == "pass" for v in per.values()) else 1
    cprompt = C.load_prompt(profile)
    c1 = C.load_c1(a.c1 or res)
    c6 = C.load_c6(a.c1 or res)
    removed = C.removed_of(c6)      # mark C6 (lead ruling R1): in force before any read (refused below otherwise)
    c7b = F.load_c7b(a.c1 or res)
    files = sorted(res.glob("transcripts_*.jsonl"))
    if a.model:
        files = [f for f in files if f.name == f"transcripts_{a.model}.jsonl"]
    if not files:
        print("read_flags45: no transcripts_*.jsonl found", file=sys.stderr)
        return 2
    spent_total, capped = 0.0, False
    for f in files:
        label = f.name[len("transcripts_"):-len(".jsonl")]
        gf = lineage.family(label)
        if not gf:
            print(f"read_flags45: {label}: family unknown; skipped", file=sys.stderr)
            continue
        try:
            picked = C.pick_readers(gf, profile, c1=c1, prompt_sha=cprompt[1], removed=removed)
        except (C.ReadersUnavailable, C.ReaderFamilyError, C.ReaderOrderUnruled, HostedGrader) as e:
            print(f"read_flags45: {label}: {e}", file=sys.stderr)
            return 2
        if any(r.hosted for r in picked) and not C.c6_current(c6):
            print(f"read_flags45: {label}: pick {[r.name for r in picked]} refused: the reader refusal mark C6 is "
                  f"absent or of the old kind in {a.c1 or res / 'marks45.json'}; run {C.C6_STEP} first; no reader "
                  "was called", file=sys.stderr)
            return 2
        readers = [r for r in picked if F.admitted(r, c7b, fprompt[1])][:2]
        if len(readers) < 2:
            print(f"read_flags45: {label}: fewer than two readers hold a stored flag pass (C7b) under flag prompt "
                  f"{fprompt[1][:12]} (have {[r.name for r in readers]}); no call made", file=sys.stderr)
            return 2
        if any(r.hosted for r in readers) and a.cap_usd is None and not a.dry_run:
            print(f"read_flags45: {label}: a hosted reader needs --cap-usd", file=sys.stderr)
            return 2
        rows = C.board_rows(f)              # the report's own rows (one loader with read45 and the report)
        units = [q for q in F.u2_questions(N.units_from_rows(rows)) if not any((q.get("status") or {}).values())]
        if a.limit:
            units = units[:a.limit]
        store = F.FlagStore(res / "readers45" / f"{label}.jsonl")
        spent = [sum(_cost(r, ln.get("usage")) for ln in store.lines for r in readers if ln.get("reader") == r.name)]
        print(f"read_flags45: {label} ({gf}): {len(units)} U2 units, readers {[r.name for r in readers]}", flush=True)
        lock = threading.Lock()

        def one(u):
            nonlocal capped
            uid = C.unit_sha(u["question"], u["reply"])
            for role, rd in enumerate(readers):
                if store.get(uid, role, rd, fprompt[1]):
                    continue
                if a.dry_run:
                    continue
                if a.cap_usd is not None and rd.hosted and spent[0] >= a.cap_usd:
                    capped = True
                    return
                v = F.read_flags(rd, u["question"], u["reply"], prompt=fprompt)
                with lock:
                    spent[0] += _cost(rd, v.get("usage"))
                flags = v["flags"]
                if flags is not None and not u["opinion"]:
                    v = {**v, "flags": {"refused": flags["refused"]}}      # position is read for opinion cards only
                store.put(uid, role, rd, fprompt[1], v, {"model": label, "unit_id": u["unit_id"], "card": u["card"],
                                                         "variant": "open"})

        with ThreadPoolExecutor(max(1, a.workers)) as ex:
            list(ex.map(one, units))
        spent_total += spent[0]
        c7 = C.c7_result(store.lines, json.loads(C.MARKS_PATH.read_text(encoding="utf-8")),
                         admitted=c7b, prompt_sha=fprompt[1])
        print(f"  C7 so far (this model only): {c7}", flush=True)
        if capped:
            break
    print(f"read_flags45: hosted spend ${spent_total:.4f}")
    if capped:
        print("read_flags45: spend cap reached; run again to resume", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
