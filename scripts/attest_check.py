#!/usr/bin/env python3
"""attest_check.py <dir>: rc 0 iff every NSFW entry under <dir> carries both attestations.

An entry is a dict in a ``cases`` / ``rungs`` / ``frames`` list of any *.json file under <dir>
(``refs/`` directories and string case ids are skipped; a pack bank lists ``items`` with an item-level ``attest``).
Each needs ``meta.attest`` (or ``attest``) with one record from ``grok`` and one from ``deepseek``, each
``{by, date, verdict: PASS, reasons}``. Files listed in ``<dir>/RETIRED.json``
(``{"files": [relative paths]}``) are skipped: they are retired from the 4.5 profile.
An empty directory is a failure (nothing was checked).

A pack set that REUSES items of a standard bank (the NSFW+ knowledge pack draws unc_know items; that bank is never
edited) is checked once it is un-staged: each item it uses needs its own ``attest`` or a side-car record in
``packs45/attest-*.json`` (``{"about", "bank": "<bank path under nsfw/>", "items": {"<item id>": {"item_sha", "attest"}}}``)
whose ``bank`` is that item's bank and whose ``item_sha`` (:func:`item_sha`) matches the item as it is in the bank
today. A mismatch (the item was edited after it was attested), a missing record or a record of another bank = not
attested.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

LISTS = ("cases", "rungs", "frames")
BANK_LIST = "items"        # a pack bank under packs45/ (item-level ``attest``); standard banks are not in scope
NEED = ("grok", "deepseek")


def _retired(d: Path) -> set[str]:
    p = d / "RETIRED.json"
    if not p.exists():
        return set()
    return {str(x) for x in json.loads(p.read_text(encoding="utf-8")).get("files", [])}


def _entry_problems(e: dict) -> list[str]:
    att = e.get("attest") or (e.get("meta") or {}).get("attest")
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


def item_sha(item: dict) -> str:
    """sha256 hex of the item's canonical JSON: keys sorted, no whitespace, UTF-8 (ensure_ascii off). The side-car's
    ``about`` line states the recipe its writer used; the two must agree (a test pins this one)."""
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def load_sidecars(d) -> dict[str, dict]:
    """{bank path under nsfw/: {item id: record}} from every ``packs45/attest-*.json`` under ``d`` (retired skipped)."""
    d = Path(d)
    skip = _retired(d)
    out: dict[str, dict] = {}
    for f in sorted(d.rglob("attest-*.json")):
        rel = f.relative_to(d).as_posix()
        if f.parent.name != "packs45" or rel in skip:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), dict) and data.get("bank"):
            out.setdefault(str(data["bank"]), {}).update(
                {str(k): v for k, v in data["items"].items() if isinstance(v, dict)})
    return out


def item_attest_problems(item: dict, bank: str, sidecars: dict) -> list[str]:
    """[] when the item carries both attestations itself, or a side-car record OF ITS OWN BANK whose item_sha matches
    the item as it is now and whose records carry both PASSes; else the problems."""
    if not _entry_problems(item):
        return []
    rec = (sidecars.get(bank) or {}).get(str(item.get("id")))
    if rec is None:
        return [f"no attestation on the item and no side-car record for it in bank {bank}"]
    if rec.get("item_sha") != item_sha(item):
        return ["side-car item_sha does not match the bank item (edited after it was attested)"]
    return [f"side-car: {m}" for m in _entry_problems({"attest": rec.get("attest")})]


def _reused_items(f: Path, data: dict, d: Path) -> tuple[str, list[dict]] | None:
    """(bank path under nsfw/, the items) of an un-staged pack set that draws a standard bank (not nsfwpack45)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crucibleforge import gen as _g, ladder, nsfwpack45 as NP
    gen = data.get("generator") or {}
    if data.get("id_prefix") not in NP.ID_PREFIX or gen.get("family") in (None, "nsfwpack45"):
        return None
    if data.get("status") == "staged":
        return None
    _g.discover()
    fam = _g.get(str(gen["family"]))
    bank = str(gen.get("bank") or getattr(fam, "BANK_DEFAULT", ""))
    bank = bank.split("nsfw/", 1)[-1] if bank.startswith("nsfw/") else bank
    ids: list[str] = []
    for c in ladder.expand_generated(data, f):
        ids += [i for i in ((_g.materialize_case(dict(c)).get("meta") or {}).get("items") or []) if i not in ids]
    try:
        items = {it.get("id"): it for it in json.loads((d / bank).read_text(encoding="utf-8")).get("items") or []}
    except (OSError, ValueError):
        items = {}
    return bank, [items.get(i) or {"id": i} for i in ids]


def problems(d) -> list[str]:
    d = Path(d)
    skip = _retired(d)
    out: list[str] = []
    n = 0
    scanned: dict[str, int] = {}               # relative file -> entries checked in it
    named: list[tuple[str, str]] = []          # (set file, bank file it names)
    sidecars = load_sidecars(d)
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
        gen = data.get("generator")
        if isinstance(gen, dict) and gen.get("family") == "nsfwpack45":
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from crucibleforge.gen import nsfwpack45
                named.append((rel, nsfwpack45.bank_key(str(gen.get("variant")))))
            except Exception as ex:                      # an unknown variant or an unimportable generator
                out.append(f"{rel}: cannot resolve the bank this generated set names ({ex})")
        if isinstance(gen, dict) and gen.get("family") not in (None, "nsfwpack45") and data.get("id_prefix"):
            try:
                reused = _reused_items(f, data, d)
            except Exception as ex:                      # noqa: BLE001 -- an unreadable reused bank fails closed
                out.append(f"{rel}: cannot resolve the items this pack set reuses ({ex})")
                reused = None
            if reused is not None:
                bank, its = reused
                for it in its:
                    n += 1
                    scanned[rel] = scanned.get(rel, 0) + 1
                    out += [f"{rel}: {it.get('id', '?')}: {m}" for m in item_attest_problems(it, bank, sidecars)]
        keys = LISTS + ((BANK_LIST,) if "packs45" in f.relative_to(d).parts[:-1] else ())
        for key in keys:
            for e in data.get(key) or []:
                if not isinstance(e, dict):
                    continue
                n += 1
                scanned[rel] = scanned.get(rel, 0) + 1
                out += [f"{rel}: {e.get('id', '?')}: {m}" for m in _entry_problems(e)]
    for setfile, bank in named:
        cands = {bank, bank.split("nsfw/", 1)[-1] if bank.startswith("nsfw/") else bank}
        if not any(scanned.get(c) for c in cands):
            out.append(f"{setfile}: the bank it names ({bank}) was not scanned (missing, retired, or no entries)")
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
