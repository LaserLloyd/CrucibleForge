"""The release accuracy check (INTENT-45; SPEC-BUILD-45 items 9 and 13): each model's own 90% range per score and
subcomponent, from a board's report.json (``models.<label>.measured.accuracy``, written by crucibleforge/accuracy45).

    python scripts/accuracy45.py <results>/report.json

Prints one line per model and score: the score, its range in points and as % of the maximum, where the range comes
from (the model's own replicate runs, the pooled replicate runs of other models, this run's repeats, or the stored
cross-run figure as a labelled estimate) and "range x% (above the 5%
limit)" when it is. Then the subcomponents. It never withholds anything and exits 0 (1 only when the file has no
accuracy block at all)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crucibleforge import accuracy45 as A  # noqa: E402

SCORES = (("code", "Code /10"), ("chat", "Chat /10"), ("overall", "Overall /20"), ("nsfw", "NSFW /10"),
          ("uncensored", "Uncensored /10"), ("nsfw_plus", "NSFW+ /20"))


def lines(report: dict) -> list[str]:
    out = []
    for label, m in sorted((report.get("models") or {}).items()):
        acc = ((m or {}).get("measured") or {}).get("accuracy")
        if not acc:
            continue
        out.append(f"{label}:")
        for k, name in SCORES:
            b = acc.get(k) or {}
            src = {"repeats": "this run's repeats", "estimate": "stored cross-run estimate",
                   "repeats+estimate": "this run's repeats + stored estimate",
                   "replicates": f"own replicate runs (n {b.get('n')})",
                   "pooled": f"replicate runs pooled across models (this model: n {b.get('n')})"}.get(b.get("source"), "none")
            out.append(f"  {name:15} {A.range_text(b)}  [{src}]" + (f"  {b['note']}" if b.get("note") else ""))
        for k, b in sorted((acc.get("segments") or {}).items()):
            out.append(f"    {k:22} {A.range_text(b)}" + (f"  {b['note']}" if b.get("note") else ""))
        im = acc.get("item_margin") or {}
        if im:
            out.append(f"  second figure, {im.get('label')}: " + ", ".join(
                f"{k} ±{v:.2f}" for k, v in im.items() if k != "label" and v is not None))
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    ls = lines(report)
    if not ls:
        print(f"{argv[0]}: no accuracy block (not a 4.5 board, or an older report)")
        return 1
    print("\n".join(ls))
    return 0


if __name__ == "__main__":
    sys.exit(main())
