#!/usr/bin/env python3
"""NSFW+ classing marks (calibration/nsfw-classing-marks-45.json). Exit 0 pass / 1 fail / 2 not measured.
Writes the `classing` block of <results>/marks45.json (the board reads it).

  C1 (every reader on the constructed set) is measured by calling the readers (resumable, stored under
     <results>/readers45/constructed-<reader>.jsonl); C2-C6 are computed from the reader store that
     scripts/read45.py filled, joined to the CURRENT units of the folder's transcripts (classing45.current_units, the
     report's own rows): a line of no current unit (an orphan) counts nowhere, each unit counts once.
  C6 (lead ruling R1): a reader over 2% is removed for the run when that stays true with every read it has not made
     assumed clean; the block (status "applied", rule classing45.C6_RULE) states the arithmetic, the removed readers
     and the verdicts that move per model (before = the graded readers, after = R1's pick); read45 and the report
     then never pick it. With no stored C1 the unmade reads are not known: "undetermined", nothing removed.

  uv run python scripts/check_classing_marks.py --results <dir> [--readers name,...] [--cap-usd N]
         [--skip-c1] [--profile P] [--config models.yaml]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from crucibleforge import classing45 as C, config  # noqa: E402
import read45  # noqa: E402

MARKS = ROOT / "calibration" / "nsfw-classing-marks-45.json"
CONSTRUCTED = ROOT / "calibration" / "nsfw-classing-constructed-45.json"


def c1_for(reader: C.ReaderSpec, prompt, res: Path, cap_usd, spent: list) -> dict:
    data = json.loads(CONSTRUCTED.read_text(encoding="utf-8"))
    want = C.constructed_branches(data)
    store = C.ReaderStore(res / "readers45" / f"constructed-{reader.name}.jsonl")
    rec = {"model": reader.model_id, "family": reader.fam, "hosted": reader.hosted, "prompt_sha": prompt[1]}
    out = []
    for a in data["asks"]:
        for v, text in a["texts"].items():
            uid = C.unit_sha(a["ask"], text)
            vote = store.get(uid, reader, prompt[1])
            if vote is None:
                if cap_usd is not None and reader.hosted and spent[0] >= cap_usd:
                    return {**rec, "status": "not_measured", "why": "cap reached"}
                vote = C.read_one(reader, a["ask"], text, sexual=bool(a.get("sexual", True)), prompt=prompt)
                if vote["reason"] == "error":
                    # a transport / provider error is no vote: the reader was not measured, it did not fail
                    return {**rec, "status": "not_measured", "why": "reader call failed: " + (vote.get("raw") or "")[:160]}
                if True:  # stored below
                    store.put(uid, reader, prompt[1], vote, {"model": "constructed", "unit_id": f"{a['id']}{v}"})
                if reader.price and vote.get("usage"):
                    spent[0] += ((vote["usage"].get("in") or 0) * reader.price["input"]
                                 + (vote["usage"].get("out") or 0) * reader.price["output"]) / 1e6
            elif vote["cls"] == "EXPLICIT":        # a stored vote is re-checked under the current quote rule
                vote = {**vote, "quote_ok": C.verify_quote(vote.get("quote", ""), text, bool(a.get("sexual", True)))}
            got = vote["cls"]
            if got == "EXPLICIT" and not vote["quote_ok"]:
                got = C.UNSURE
            out.append({"variant": v, "want": want[v], "got": got})
    return {**rec, **C.c1_result(out)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--readers")
    ap.add_argument("--cap-usd", type=float)
    ap.add_argument("--skip-c1", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    res = Path(a.results)
    from crucibleforge import marksindex45
    marksindex45.require(MARKS)
    marks = json.loads(MARKS.read_text(encoding="utf-8"))
    profile = read45.load_profile(a.profile)
    if a.readers:
        profile = read45.restrict(profile, {x.strip().lower() for x in a.readers.split(",")})
    C.set_config(config.load_config(a.config) if a.config else None)
    prompt = C.load_prompt(profile)
    block: dict = {"prompt_sha": prompt[1], "marks_file": str(MARKS.name)}
    spent = [0.0]
    mp = res / "marks45.json"
    cur = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else {}
    if a.skip_c1:
        # the stored C1 results admit readers (classing45.pick_readers): a run without C1 keeps them
        if (cur.get("classing") or {}).get("C1_constructed"):
            block["C1_constructed"] = cur["classing"]["C1_constructed"]
    else:
        cands = C.candidates(profile)
        c1 = {}
        if not cands:
            print(f"C1: {C.NOT_NAMED}", flush=True)
        for r in cands:
            if r.hosted and not r.price:
                c1[r.name] = {"model": r.model_id, "status": "not_measured", "why": "hosted reader without a price"}
                continue
            if r.hosted and a.cap_usd is None:
                c1[r.name] = {"model": r.model_id, "status": "not_measured", "why": "hosted reader needs --cap-usd"}
                continue
            c1[r.name] = c1_for(r, prompt, res, a.cap_usd, spent)
            print(f"C1 {r.name}: {c1[r.name]['status']} {c1[r.name].get('branches', '')}", flush=True)
        # roles not run this time keep their stored result (each record names its model id + prompt sha)
        prev = ((cur.get("classing") or {}).get("C1_constructed") or {}).get("per_reader") or {}
        c1 = {**prev, **c1}
        st = [v["status"] for v in c1.values()]
        block["C1_constructed"] = {"status": ("fail" if "fail" in st and "pass" not in st else
                                              "pass" if "pass" in st else "not_measured"), "per_reader": c1,
                                   "note": "a reader that fails C1 is not admitted; the mark passes while two "
                                           "readers of distinct families pass"}
        passed_fams = {v.get("family") for v in c1.values() if v.get("status") == "pass" and v.get("prompt_sha") == prompt[1]}
        if len(passed_fams) < 2 and "pass" in st:
            block["C1_constructed"]["status"] = "fail"
    # C2-C6 from the reader store joined to the current units, each model's readers as graded (no removal) and R1's
    # pick after it: the same in-memory computation release45 runs before any read (classing45.store_marks)
    c1_now = [dict(v) for v in ((block.get("C1_constructed") or {}).get("per_reader") or {}).values()
              if isinstance(v, dict)]
    units = C.combined_units([res])
    block.update(C.store_marks([res], profile, marks, c1=c1_now))
    block["units_current"] = len(units)
    block["c1_hosted_spend_usd"] = round(spent[0], 4)
    # C6 "applied" = the rule ran (a reader was removed); "undetermined" = more reads are needed to decide
    smap = {"applied": "pass", "undetermined": "not_measured"}
    status = [smap.get(v["status"], v["status"]) for v in block.values() if isinstance(v, dict) and "status" in v]
    block["overall"] = "fail" if "fail" in status else "not_measured" if "not_measured" in status else "pass"
    # the flag lane's stored C7b results (scripts/read_flags45.py) live in the same block: never dropped by a C1 run
    keep = (cur.get("classing") or {}).get("C7_constructed")
    if keep:
        block["C7_constructed"] = keep
    cur["classing"] = block
    mp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    for k, v in block.items():
        if isinstance(v, dict) and "status" in v:
            print(f"{k}: {v['status']}")
    print("overall:", block["overall"])
    return {"pass": 0, "fail": 1, "not_measured": 2}[block["overall"]]


if __name__ == "__main__":
    sys.exit(main())
