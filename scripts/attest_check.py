#!/usr/bin/env python3
"""attest_check.py <dir>: rc 0 iff every NSFW entry under <dir> carries both attestations.

An entry is a dict in a ``cases`` / ``rungs`` / ``frames`` list of any *.json file under <dir>
(``refs/`` directories and string case ids are skipped). Each needs
``meta.attest`` with one record from ``grok`` and one from ``deepseek``, each
``{by, date, verdict: PASS, reasons}``. Files listed in ``<dir>/RETIRED.json``
(``{"files": [relative paths]}``) are skipped: they are retired from the 4.5 profile.
An empty directory is a failure (nothing was checked).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LISTS = ("cases", "rungs", "frames")
NEED = ("grok", "deepseek")


def _retired(d: Path) -> set[str]:
    p = d / "RETIRED.json"
    if not p.exists():
        return set()
    return {str(x) for x in json.loads(p.read_text(encoding="utf-8")).get("files", [])}


def _entry_problems(e: dict) -> list[str]:
    att = (e.get("meta") or {}).get("attest")
    if not isinstance(att, list) or not att:
        return ["no meta.attest"]
    out = []
    for need in NEED:
        recs = [a for a in att if isinstance(a, dict) and str(a.get("by", "")).lower() == need]
        if not recs:
            out.append(f"no {need} attestation")
            continue
        for a in recs:
            if str(a.get("verdict", "")).upper() != "PASS":
                out.append(f"{need} verdict {a.get('verdict')!r} (need PASS)")
            if not str(a.get("date") or "").strip():
                out.append(f"{need} attestation has no date")
    return out


def problems(d) -> list[str]:
    d = Path(d)
    skip = _retired(d)
    out: list[str] = []
    n = 0
    for f in sorted(d.rglob("*.json")):
        rel = f.relative_to(d).as_posix()
        if "refs" in f.relative_to(d).parts[:-1] or rel in skip or rel == "RETIRED.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            out.append(f"{rel}: unreadable ({ex})")
            continue
        if not isinstance(data, dict):
            continue
        for key in LISTS:
            for e in data.get(key) or []:
                if not isinstance(e, dict):
                    continue
                n += 1
                out += [f"{rel}: {e.get('id', '?')}: {m}" for m in _entry_problems(e)]
    if n == 0:
        out.append(f"{d}: no case, rung or frame found to check")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    ps = problems(argv[0])
    for p in ps:
        print("FAIL", p)
    if not ps:
        print("attest_check: ok")
    return 1 if ps else 0


if __name__ == "__main__":
    sys.exit(main())
