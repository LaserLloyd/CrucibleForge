#!/usr/bin/env python3
"""Fill the NSFW+ reader store (spec 4.3): class every stored sexual / dark unit of a results folder with the
configured readers. Resumable (a line already in <results>/readers45/<model>.jsonl is never asked again),
reads stored replies only (never generates), skips units already classed.

  uv run python scripts/read45.py --results <dir> [--readers role,role] [--cap-usd N] [--workers K]
                                  [--profile bench45 | path] [--c1 marks45.json] [--config models.yaml]
                                  [--model LABEL] [--limit N] [--dry-run] [--pack-cap-usd N]

Readers: the profile's roles (composition merged, the same loader the report uses), each named by an indexed
reader addendum and admitted only by a stored C1 pass for its exact model id + prompt sha (``--c1``, default
<results>/marks45.json, written by check_classing_marks.py). No default reader; a hosted reader needs a price.

NSFW+ pack units (rows of the six pack sets, crucibleforge/nsfwpack45.py) are read by the same admitted readers and
pick rules into their own store <results>/readers45/nsfwpacks45/<model>.jsonl (never the standard store), keyed on
content (a re-stamp repeats no call). A hosted reader that would be called for a pack unit needs --pack-cap-usd
(its own cap and ledger): without it the run stops with exit 2 before any reader call. A folder whose pack units
are all read needs no pack cap.

The C6 removal (lead ruling R1) must be in force before any read: a pick holding a hosted reader is refused (exit 2,
no call) while the C6 block of --c1 is absent or of the old kind (no ``rule``); release45 step b-c6 computes it from
the combined reader store first. A model whose family is left with fewer than two readers is PENDING: its units are
not read, the reason is printed and written to readers45/summary.json, and the other models are read.

Exit 0 done, 2 nothing to read / a reader set that cannot be used (refused, no call made), 3 spend cap reached (resume
to continue), 4 done with pending: every other model was read, the named models wait for readers."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucibleforge import adult45, classing45 as C, config, lineage  # noqa: E402
from crucibleforge import nsfwpack45 as NP  # noqa: E402
from crucibleforge.judge45 import HostedGrader  # noqa: E402

NSFW_VARIANTS = ("write", "dwell", "session", "taboo")
UNC_VARIANTS = ("uask", "dark")


def load_profile(path):
    """The one loader (classing45.load_profile): the same composition-merged profile the report reads."""
    return C.load_profile(path)


def restrict(profile: dict, names: set[str]) -> dict:
    """The profile with its reader roles cut to ``names`` (role names, case-insensitive)."""
    cfg = dict(C._classing_cfg(profile))
    rd = dict(C.reader_roles(profile))
    rd["set"] = [r for r in rd["set"] if r.lower() in names]
    rd["hosted_fallback"] = [r for r in rd["hosted_fallback"] if r.lower() in names]
    rd["local"] = [r for r in rd["local"] if r.lower() in names]
    return {**profile, "nsfw_plus": {**(profile.get("nsfw_plus") or {}), "classing": {**cfg, "readers": rd}}}


class Ledger:
    def __init__(self, store_lines, specs, cap):
        self.cap, self.spent, self.lock = cap, 0.0, threading.Lock()
        self.price = {s.name: s.price for s in specs if s.price}
        for ln in store_lines:
            self.add(ln["reader"], ln["vote"].get("usage"))

    def add(self, reader, usage):
        p = self.price.get(reader)
        if p and usage:
            with self.lock:
                self.spent += ((usage.get("in") or 0) * p["input"] + (usage.get("out") or 0) * p["output"]) / 1e6

    def over(self):
        return self.cap is not None and self.spent >= self.cap


def pack_units_to_read(rows: list[dict]) -> list[dict]:
    """The NSFW+ pack units of the rows that the readers class (nsfwpack45.units_from_rows, classed variants)."""
    return [u for u in NP.units_from_rows(rows) if u.get("classing")]


def pack_calls_needed(units: list[dict], readers, store, gf: str, prompt) -> list:
    """The readers a pack read would call (a vote missing from the pack store), found offline: no reader is called."""
    missing: list = []

    def probe(reader, messages, **kw):
        missing.append(reader)
        raise C.ReaderMissing(reader.name)
    for u in units:
        cl = u["classing"]
        if C.unit_pre_read(u) is not None or adult45.detector(cl["reply"]):
            continue
        try:
            C.class_unit(cl["ask"], cl["reply"], cl["sexual"], gf, readers, store, prompt=prompt, chat=probe)
        except C.ReaderMissing:
            pass
    return missing


def family_shortage(profile, c1, prompt_sha, removed) -> bool:
    """True when two readers of distinct families ARE admitted for some family (the shortage is this graded family's:
    its own family, or a C6 removal), False when no reader set exists at all (not named / not admitted: exit 2)."""
    try:
        C.pick_readers("no-such-family", profile, c1=c1, prompt_sha=prompt_sha, removed=removed)
    except (C.ReadersUnavailable, C.ReaderFamilyError, C.ReaderOrderUnruled, HostedGrader):
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--readers", help="comma list of reader names to use (default: every configured reader)")
    ap.add_argument("--cap-usd", type=float, help="hosted spend cap; required when a hosted reader would be used")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--profile")
    ap.add_argument("--c1", help="the marks45.json holding the stored C1 results (default: <results>/marks45.json)")
    ap.add_argument("--config")
    ap.add_argument("--model", help="only this transcripts label")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pack-cap-usd", type=float, help="hosted spend cap of the NSFW+ pack units (their own ledger); "
                                                        "required when a hosted reader would be called for one")
    a = ap.parse_args(argv)
    res = Path(a.results)
    profile = load_profile(a.profile)
    if a.readers:
        profile = restrict(profile, {x.strip().lower() for x in a.readers.split(",")})
    C.set_config(config.load_config(a.config) if a.config else None)
    prompt = C.load_prompt(profile)
    c1 = C.load_c1(a.c1 or res)
    # mark C6 (lead ruling R1): a reader removed for the run is never picked; its family may be left with one reader
    c6 = C.load_c6(a.c1 or res)
    removed = C.removed_of(c6)
    summary, spent_total, capped = {}, 0.0, False
    pending: dict = {}
    files = sorted(res.glob("transcripts_*.jsonl"))
    if a.model:
        files = [f for f in files if f.name == f"transcripts_{a.model}.jsonl"]
    if not files:
        print("read45: no transcripts_*.jsonl found", file=sys.stderr)
        return 2
    for f in files:
        label = f.name[len("transcripts_"):-len(".jsonl")]
        gf = lineage.family(label)
        if not gf:
            print(f"read45: {label}: family unknown (lineage.FAMILIES / TUNES); skipped", file=sys.stderr)
            continue
        try:
            readers = C.pick_readers(gf, profile, c1=c1, prompt_sha=prompt[1], removed=removed)
        except HostedGrader as e:                 # owner 2026-11-21: no hosted grader; nothing was called
            print(f"read45: {label}: {e}", file=sys.stderr)
            return 2
        except C.ReadersUnavailable as e:
            if not family_shortage(profile, c1, prompt[1], removed):
                print(f"read45: {label}: {e}", file=sys.stderr)          # no reader set at all: nothing to pend on
                return 2
            # R1: a family left with fewer than two readers is pending; the other models are still read
            pending[label] = f"pending readers: {e}"
            summary[label] = {"family": gf, "pending": pending[label], "readers_removed": removed}
            print(f"read45: {label} ({gf}): {pending[label]}", file=sys.stderr, flush=True)
            continue
        except (C.ReaderFamilyError, C.ReaderOrderUnruled) as e:
            print(f"read45: {label}: {e}", file=sys.stderr)
            return 2
        if any(r.hosted for r in readers) and not C.c6_current(c6):
            print(f"read45: {label}: pick {[r.name for r in readers]} refused: the reader refusal mark C6 is "
                  f"{'absent' if c6 is None else 'of the old kind (no rule)'} in {a.c1 or res / 'marks45.json'}, so "
                  f"a removed reader could be bought; run {C.C6_STEP} first; no reader was called", file=sys.stderr)
            return 2
        if any(r.hosted for r in readers) and a.cap_usd is None and not a.dry_run:
            print(f"read45: {label}: a hosted reader ({[r.name for r in readers if r.hosted]}) needs --cap-usd",
                  file=sys.stderr)
            return 2
        # the report's own rows (classing45.board_rows / pack_rows): the readers read exactly the report's units
        rows = C.board_rows(f)
        pack_todo = pack_units_to_read(C.pack_rows(f))
        pstore = C.ReaderStore(NP.store_path(res, label)) if pack_todo else None
        need = pack_calls_needed(pack_todo, readers, pstore, gf, prompt) if pack_todo else []
        # fail closed: any pack vote still missing while any picked reader is hosted needs the pack cap
        if need and a.pack_cap_usd is None and not a.dry_run and any(r.hosted for r in readers):
            print(f"read45: {label}: NSFW+ pack unit(s) still to read with a hosted reader "
                  f"({sorted(r.name for r in readers if r.hosted)}): needs --pack-cap-usd (no reader was called)",
                  file=sys.stderr)
            return 2
        units = C.units_from_rows(rows)
        if a.limit:
            units = units[:a.limit]
        store = C.ReaderStore(res / "readers45" / f"{label}.jsonl")
        ledger = Ledger(store.lines, readers, a.cap_usd)
        print(f"read45: {label} ({gf}): {len(units)} units, readers {[r.name for r in readers]}"
              f"{' (hosted: ' + ','.join(r.name for r in readers if r.hosted) + ')' if any(r.hosted for r in readers) else ''}",
              flush=True)
        gates: dict = {"nsfw": [], "unc": []}
        lock = threading.Lock()

        def one(u):
            nonlocal capped
            pre = C.unit_pre_read(u)
            verdict = None
            refusal = False
            if pre is None:
                refusal = adult45.detector(u["reply"])
                if not refusal:
                    if ledger.over():
                        capped = True
                        return
                    if a.dry_run:
                        return
                    verdict = C.class_unit(u["ask"], u["reply"], u["sexual"], gf, readers, store, prompt=prompt,
                                           meta={"model": label, "unit_id": u["unit_id"], "seed": u["seed"],
                                                 "seed_set": u["seed_set"], "card": u["card"],
                                                 "variant": u["variant"]})
            g = C.gate_result(verdict, refusal_phrase=refusal, empty=(pre == "empty"), cut=(pre == "cut"),
                              finish_reason="content_filter" if pre == "filtered" else None)
            with lock:
                (gates["nsfw"] if u["variant"] in NSFW_VARIANTS else gates["unc"]).append(g)

        with ThreadPoolExecutor(max(1, a.workers)) as ex:
            list(ex.map(one, units))
        ledger.spent = Ledger(store.lines, readers, a.cap_usd).spent
        spent_total += ledger.spent
        pack_counts = None
        if pack_todo and not capped:
            pl = Ledger(pstore.lines, readers, a.pack_cap_usd)
            pg: list = []
            for u in pack_todo:
                cl = u["classing"]
                pre = C.unit_pre_read(u)
                verdict, refusal = None, False
                if pre is None:
                    refusal = adult45.detector(cl["reply"])
                    if not refusal:
                        if pl.over():
                            capped = True
                            break
                        if a.dry_run:
                            continue
                        verdict = C.class_unit(cl["ask"], cl["reply"], cl["sexual"], gf, readers, pstore,
                                               prompt=prompt, meta=NP.record_meta(u, label))
                        pl = Ledger(pstore.lines, readers, a.pack_cap_usd)
                pg.append(C.gate_result(verdict, refusal_phrase=refusal, empty=(pre == "empty"), cut=(pre == "cut"),
                                        finish_reason="content_filter" if pre == "filtered" else None))
            spent_total += pl.spent
            pack_counts = {"counts45": C.counts45(pg), "hosted_spend_usd": round(pl.spent, 4), "units": len(pack_todo)}
            print(f"  nsfw+ packs: {C.format_counts(pack_counts['counts45'])}", flush=True)
        summary[label] = {"family": gf, "readers": [r.stamp(prompt[1]) for r in readers],
                          "readers_removed": removed,
                          "counts45": {k: C.counts45(v) for k, v in gates.items()},
                          "hosted_spend_usd": round(ledger.spent, 4)}
        if pack_counts is not None:
            summary[label]["nsfw_packs45"] = pack_counts
        for k, v in gates.items():
            if v:
                print(f"  {k}: {C.format_counts(C.counts45(v))}", flush=True)
        if capped:
            break
    (res / "readers45").mkdir(exist_ok=True)
    (res / "readers45" / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"read45: hosted spend ${spent_total:.4f}")
    if capped:
        print("read45: spend cap reached; run again to resume", file=sys.stderr)
        return 3
    if pending:
        print(f"read45: done with pending: {', '.join(sorted(pending))} wait for readers", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
