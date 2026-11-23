"""Plan and store the long chat scenes' second reading, one reader per model, family rules enforced.

usage: python scripts/read_chat45.py --results <dir> --folders <run folder>... [--unavailable <id>...] [--no-hosted]
           [--import-agree] [--packs]

Default: print, per folder, the reader pick (calibration/chat-readers-45.yaml: local first, hosted fallback stamped) or
"NO FITTING READER", and the exact agree command that makes the calls (the rig lease, client and spend cap live
there: `crucibleforge agree --parts chat/j_rplong --reader <id> --reader-thinking on [--cap-usd N] <f>`).
--import-agree: copy the long-scene records those runs left in <folder>/agree/ into <results>/readers45/<model>.jsonl
(no call; --stored-reader <id> when that run named the same reader by an engine alias). The store is resumable: a stored (unit, reader, prompt, row) is never listed twice.
--packs: the same for the Chat PACK scenes (crucibleforge/packs45.py): the plan line names `agree --parts chat/packs45`,
which writes its records straight into <folder>/readers45/ (no import step); --verify exits 1 unless every pack scene
present in the rows has a record from the pinned reader. A folder with no pack scene prints "no pack scenes"."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucibleforge import agree as AG            # noqa: E402
from crucibleforge import packs45 as PK          # noqa: E402
from crucibleforge import rplong_mean as RM      # noqa: E402


def engine_model_id(label: str, config_path: str | None = None) -> str | None:
    """The model id a ``crucibleforge agree --reader <label>`` call resolves a models-yaml label to (the id its record
    files are named after), read without side effects; None when the label is not in the file."""
    import os
    import yaml
    p = config_path or os.environ.get("CRUCIBLEFORGE_CONFIG")
    if not p or not Path(p).is_file():
        return None
    for m in (yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}).get("models") or []:
        if m.get("name") == label:
            return m.get("model_id") or None
    return None


def stored_reader_id(folder: Path, reader, explicit: str | None, config_path: str | None = None) -> str | None:
    """The reader id the agree run filed its records under. A local reader is named in the readers file by its
    models-yaml label, but ``agree`` files its records under the model id the label resolves to (pilot 2 on the
    DeepSeek folders: 8 scenes read, 0 records imported). A hosted reader's label is its own id."""
    if explicit:
        return explicit
    if AG.load_records(Path(folder), reader.key):
        return None
    mid = engine_model_id(reader.id, config_path)
    return mid if mid and mid != reader.id else None


def _packs(a, cfg, f, label: str, rows: list[dict]) -> int:
    units = PK.pack_units(rows)
    if not units:
        print(f"{label}: no pack scenes")
        return 0
    gf = AG.graded_family(label, next(iter(units.values())))
    try:
        rd = RM.pinned_reader(gf, cfg, allow_hosted=not a.no_hosted, unavailable=set(a.unavailable))
    except RM.NoFittingReader as e:
        print(f"{label}: NO FITTING READER ({e}); pack scenes print pending")
        return 1
    print(f"{label}: reader {rd.id} ({rd.family}{', hosted: needs --cap-usd' if rd.hosted else ''}); run: "
          f"crucibleforge agree --parts {PK.PACKS_PART} --reader {rd.id} --reader-thinking on {f}")
    if not a.verify:
        return 0
    flat = list(RM.load_store(f, label).values())
    sc = PK.scene_scores(rows, flat, label=label, graded_family=gf, reader_for=rd)
    missing = sorted(u for u, e in sc.items() if e["status"] != "ok" and e["judge"] is not None)
    print(f"{label}: {len(sc) - len(missing)} of {len(sc)} pack scene(s) have both readings"
          + (f"; unread: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--unavailable", nargs="*", default=[])
    ap.add_argument("--no-hosted", action="store_true")
    ap.add_argument("--import-agree", action="store_true")
    ap.add_argument("--stored-reader", default=None,
                    help="with --import-agree: the reader id the agree run used (an alias of the picked reader)")
    ap.add_argument("--verify", action="store_true",
                    help="exit 1 unless every judged long scene has a record from the pinned reader (no call)")
    ap.add_argument("--packs", action="store_true", help="plan / verify the Chat pack scenes instead of the standard ones")
    ap.add_argument("--config", default=None)
    ap.add_argument("--config-models", default=None, help="the models yaml (default $CRUCIBLEFORGE_CONFIG)")
    a = ap.parse_args(argv)
    cfg = RM.load_readers(a.config)
    rc = 0
    for f in a.folders:
        label, rows = AG.read_folder(Path(f))
        if a.packs:
            rc = max(rc, _packs(a, cfg, f, label, rows))
            continue
        gf = AG.graded_family(label, next(iter(AG.long_units(rows).values()), None))
        try:
            rd = RM.pinned_reader(gf, cfg, allow_hosted=not a.no_hosted, unavailable=set(a.unavailable))
        except RM.NoFittingReader as e:
            print(f"{label}: NO FITTING READER ({e}); long scenes print pending")
            rc = max(rc, 1)
            continue
        print(f"{label}: reader {rd.id} ({rd.family}{', hosted: needs --cap-usd' if rd.hosted else ''}); run: "
              f"crucibleforge agree --parts {AG.LONG_PART} --reader {rd.id} --reader-thinking on {f}")
        if a.verify:
            flat = list(RM.load_store(a.results, label).values())
            sc = RM.scene_scores(rows, flat, label=label, graded_family=gf, reader_for=rd)
            missing = sorted(u for u, e in sc.items() if e["status"] != "ok" and e["judge"] is not None)
            print(f"{label}: {len(sc) - len(missing)} of {len(sc)} long scene(s) have both readings"
                  + (f"; unread: {', '.join(missing)}" if missing else ""))
            if missing:
                rc = max(rc, 1)
        if a.import_agree:
            have = RM.load_store(a.results, label)
            new = []
            for r in RM.import_agree_records(f, rd, stored_reader_id(Path(f), rd, a.stored_reader, a.config_models), rows):
                k = (r["unit"], r["reader"], (r["reader_stamp"] or {}).get("prompt_sha"), r.get("row_sha"))
                # a later reading that scored replaces a stored one that did not (a scene the reader first failed on)
                if k not in have or (have[k].get("score_reader") is None and r.get("score_reader") is not None):
                    new.append(r)
            if new:
                RM.append_store(a.results, label, new)
            print(f"{label}: {len(new)} record(s) stored")
    return rc


if __name__ == "__main__":
    sys.exit(main())
