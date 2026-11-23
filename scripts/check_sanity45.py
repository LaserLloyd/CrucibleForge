#!/usr/bin/env python3
"""Board sanity block (plan B4): the defect class that hid G1 (sessions and dark scenes read on the wrong reply, so
every model got the same class and Adult role-play was 0 for everyone) listed before a board is rendered.

  check_sanity45.py --results <board dir> --folders <run folder> ... [--profile bench45] [--stored-meta]
                    [--no-write]

Entries (each with a stable ``code`` the lead names in ``release45 --accept-sanity <code>``):

  same-class:<variant>       every classed unit of that variant has the same final class for every model (at least
                             two models with a verdict): readers reading the wrong text look exactly like this
  empty-read:<model>:<unit>  a stored reader line for a unit whose scored region is empty (never a vote)
  id-collision:<model>       two units of one model got the same unit id (classing45.DuplicateUnitId)
  zero:<segment>[/<part>]    a segment, or a part of an NSFW+ segment, that is 0.0 for every measured model (at
                             least two models), read from each folder's report.json marks_input45

Final classes are R1's: the current units of the board (classing45.combined_units), each unit's lines placed by the
pick after the C6 removal. ``--stored-meta`` instead groups the store lines by the unit and variant they were
written for and classes them by their stored roles: the board as it was graded (for a before / after comparison).

Writes ``sanity`` into <board>/marks45.json (other blocks kept). Exit 0 no entry, 1 entries listed, 3 bad input.
Reads stored data only: no reader, model or network call."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucibleforge import classing45 as C  # noqa: E402
from crucibleforge import marksinput45 as MI  # noqa: E402


# ------------------------------------------------------------------ entry builders (pure)
def same_class_entries(verdicts) -> list[dict]:
    """``verdicts`` = [(model, variant, cls)]: a variant whose every verdict has one class, over two models or more."""
    by: dict = {}
    for m, v, c in verdicts:
        by.setdefault(v, []).append((m, c))
    out = []
    for v in sorted(by):
        models = {m for m, _ in by[v]}
        classes = {c for _, c in by[v]}
        if len(models) >= 2 and len(classes) == 1:
            out.append({"code": f"same-class:{v}",
                        "detail": f"every {v} unit of every model is {next(iter(classes))} ({len(by[v])} units, "
                                  f"{len(models)} models)"})
    return out


def zero_entries(values: dict) -> list[dict]:
    """``values`` = {label: {key: points}}: a key that is 0.0 for every model that has a value (two or more)."""
    keys = sorted({k for v in values.values() for k in v})
    out = []
    for k in keys:
        got = [float(v[k]) for v in values.values() if v.get(k) is not None]
        if len(got) >= 2 and all(x == 0.0 for x in got):
            out.append({"code": f"zero:{k}", "detail": f"{k} is 0.0 for every measured model ({len(got)} models)"})
    return out


# ------------------------------------------------------------------ inputs
def store_entries(res: Path) -> list[dict]:
    """empty-read and id-collision entries of one dir's transcripts and reader stores."""
    from crucibleforge import nsfwpack45 as NP
    out = []
    lines = C.store_lines([res])
    for f in sorted(Path(res).glob("transcripts_*.jsonl")):
        label = f.name[len("transcripts_"):-len(".jsonl")]
        try:
            units = C.units_from_rows(C.board_rows(f))
            NP.units_from_rows(C.pack_rows(f))
        except C.DuplicateUnitId as e:
            out.append({"code": f"id-collision:{label}", "detail": str(e)})
            continue
        shas = {ln.get("unit") for ln in lines if ln.get("model") == label}
        for u in units:
            if u.get("ask") is None or C.unit_pre_read(u) != "empty":
                continue
            if {C.unit_sha(u["ask"], u.get("reply") or ""), C.unit_sha(u["ask"], "")} & shas:
                out.append({"code": f"empty-read:{label}:{u['unit_id']}",
                            "detail": f"{label} {u['unit_id']}: a reader line is stored for an empty scored region"})
    return out


def verdicts_now(dirs, profile, c1) -> list[tuple]:
    """[(model, variant, cls)] of the current units under R1's pick after the C6 removal."""
    units = C.combined_units(dirs)
    pick = C.picker(profile, c1)
    before = {m: pick(m, None) for m in sorted({u["model"] for u in units})}
    table = C._unit_table(C.store_lines(dirs), units)
    c6, after = C._c6(table, before, pick)
    rm = c6.get("removed") or []
    out = []
    for u, ls in table:
        act = [x for x in ls if not C._line_removed(x, rm)]
        specs = after.get(u["model"])
        cls = C._final(C._picked(act, specs) if specs is not None else C._roles(act))
        if cls is not None:
            out.append((u["model"], u["variant"], cls))
    return out


def verdicts_stored(dirs) -> list[tuple]:
    """[(model, variant, cls)] as the board was graded: the lines grouped by their unit sha, classed by their
    stored roles, variant from the stored line."""
    table = C._unit_table(C.store_lines(dirs), None)
    out = []
    for u, ls in table:
        cls = C._final(C._roles(ls))
        var = next((x.get("variant") for x in ls if x.get("variant")), None)
        if cls is not None and var:
            out.append((u["model"], var, cls))
    return out


def segment_values(folders) -> dict:
    """{label: {key: points}} from each folder's report.json marks_input45 (the mean over a label's runs that carry
    the key); the NSFW+ segments' parts as ``<segment>/<part>``."""
    acc: dict = {}
    for f in folders:
        try:
            models = (json.loads((Path(f) / "report.json").read_text(encoding="utf-8")).get("models") or {})
        except (OSError, ValueError):
            continue
        for label, m in models.items():
            segs = ((m.get("marks_input45") or {}).get("segments") or {})
            for k, g in segs.items():
                if not isinstance(g, dict):
                    continue
                if g.get("points") is not None:
                    acc.setdefault(label, {}).setdefault(k, []).append(float(g["points"]))
                if MI.adult_key(k):
                    for pk, pv in (g.get("parts") or {}).items():
                        if pv is not None:
                            acc.setdefault(label, {}).setdefault(f"{k}/{pk}", []).append(float(pv))
    return {l: {k: sum(v) / len(v) for k, v in d.items()} for l, d in acc.items()}


def c1_of(res: Path) -> list[dict]:
    return C.load_c1(res)


def sanity(board: Path, folders, profile, stored_meta: bool = False) -> dict:
    entries = []
    for d in [board, *folders]:
        if any(Path(d).glob("transcripts_*.jsonl")):
            entries += [e for e in store_entries(Path(d)) if e["code"] not in {x["code"] for x in entries}]
    vs = verdicts_stored([board]) if stored_meta else verdicts_now([board], profile, c1_of(board))
    entries += same_class_entries(vs)
    entries += zero_entries(segment_values(folders))
    return {"entries": entries, "mode": "stored-meta" if stored_meta else "current",
            "checked": {"verdicts": len(vs), "folders": [str(f) for f in folders]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", required=True, help="the combined board dir")
    ap.add_argument("--folders", nargs="*", default=[])
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--stored-meta", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    board = Path(a.results)
    if not board.is_dir():
        print(f"check_sanity45: not a folder: {board}", file=sys.stderr)
        return 3
    blk = sanity(board, [Path(f) for f in a.folders], C.load_profile(a.profile), stored_meta=a.stored_meta)
    for e in blk["entries"]:
        print(f"SANITY {e['code']}: {e['detail']}")
    print(f"sanity: {len(blk['entries'])} entr{'y' if len(blk['entries']) == 1 else 'ies'} "
          f"({blk['mode']}, {blk['checked']['verdicts']} verdicts)")
    if not a.no_write:
        p = board / "marks45.json"
        try:
            cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            cur = {}
        cur = cur if isinstance(cur, dict) else {}
        cur["sanity"] = blk
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    return 1 if blk["entries"] else 0


if __name__ == "__main__":
    sys.exit(main())
