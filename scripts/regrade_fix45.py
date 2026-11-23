#!/usr/bin/env python3
"""Re-grade stored single-turn fix replies under the current bug-fix checker (fixrows45.checker_stamp).

  uv run python scripts/regrade_fix45.py --results <dir> [--model LABEL] [--out <dir>]

Reads <results>/transcripts_*.jsonl, never generates. A fix row whose ``fixcheck.stamp`` differs from the current
checker stamp is graded again from its stored reply (fixrows45.reply_verdict). Without --out nothing is written and
the counts are printed; with --out each model's rows (re-graded ones replaced) go to <out>/transcripts_<model>.jsonl.
The bug-hunt agent rows (CB-F1) cannot be re-graded: their final file tree is not stored, so they stay pending until
they are generated again, and the output says so.

Exit 0 done, 2 no transcripts, 3 a re-grade could not run (no sandbox here)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crucibleforge import fixrows45 as FR  # noqa: E402


def _case(row: dict) -> dict | None:
    cfg = row.get("grader_config")
    if not cfg:
        try:
            from crucibleforge import chains
            inst = chains.block_instance(row)
            cfg = chains.materialize(inst).get("grader_config") if inst else None
        except Exception:          # noqa: BLE001 -- not rebuildable here: reported, never guessed
            cfg = None
    return {"id": row.get("case_id"), "grader_config": cfg} if cfg else None


def regrade_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    """(rows with stale single-turn fix verdicts replaced, counts)."""
    cur = FR.checker_stamp()
    n = {"current": 0, "regraded": 0, "changed": 0, "agent_not_regradable": 0, "no_key": 0, "no_sandbox": 0}
    out = []
    for r in rows:
        fc = r.get("fixcheck")
        if not isinstance(fc, dict) or fc.get("stamp") == cur or r.get("grade") == "error":
            n["current"] += isinstance(fc, dict) and fc.get("stamp") == cur
            out.append(r)
            continue
        if str(r.get("case_id") or "").startswith("CB-F1-"):
            n["agent_not_regradable"] += 1
            out.append(r)
            continue
        case = _case(r)
        if case is None:
            n["no_key"] += 1
            out.append(r)
            continue
        m = r.get("metrics") or {}
        v = FR.reply_verdict(case, r.get("response") or "", finish_reason=r.get("finish_reason"),
                             completion_tokens=m.get("completion_tokens"), sent=r.get("max_tokens_sent"))
        if v is None or v.get("unavailable"):
            n["no_sandbox"] += 1
            out.append(r)
            continue
        n["regraded"] += 1
        n["changed"] += float(v.get("score") or 0) != float(fc.get("score") or 0)
        out.append({**r, "fixcheck": v})
    return out, n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--model")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    files = sorted(Path(a.results).glob("transcripts_*.jsonl"))
    if a.model:
        files = [f for f in files if f.name == f"transcripts_{a.model}.jsonl"]
    if not files:
        print("regrade_fix45: no transcripts found", file=sys.stderr)
        return 2
    rc = 0
    print(f"regrade_fix45: current checker stamp {FR.checker_stamp()}")
    for f in files:
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        new, n = regrade_rows(rows)
        label = f.name[len("transcripts_"):-len(".jsonl")]
        print(f"{label}: {n['regraded']} re-graded ({n['changed']} changed score), {n['current']} already current, "
              f"{n['no_key']} without an answer key, {n['no_sandbox']} not graded (no sandbox)")
        if n["agent_not_regradable"]:
            print(f"{label}: {n['agent_not_regradable']} bug-hunt agent row(s) cannot be re-graded (the final file "
                  "tree is not stored); they stay pending until generated again")
        if n["no_sandbox"]:
            rc = 3
        if a.out:
            od = Path(a.out)
            od.mkdir(parents=True, exist_ok=True)
            (od / f.name).write_text("".join(json.dumps(r) + "\n" for r in new), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
