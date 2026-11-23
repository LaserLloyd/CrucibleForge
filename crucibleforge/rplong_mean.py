"""The eight long chat scenes under 4.5: score = the mean of the local judge and ONE second reader.

New module (grader-stamp guard, SPEC-REALIGN-45 4.1): nothing here edits a grading module. The judge's verdicts stay
in the stored rows; the second reader's verdicts live in the reader store (``<results>/readers45/<model>.jsonl``, one
line per unit, reader and prompt); scoring is report-time code over both.

Rules (SPEC-REALIGN-45 section 3):

- reader family != judge family != graded model family (:func:`pick_reader`, :func:`check_fit`);
- readers are configured (``calibration/chat-readers-45.yaml``), open-weight local readers first, a hosted reader only as
  the stamped fallback; every verdict carries a ``reader_stamp``;
- readers think (thinking on, temperature 0, JSON output);
- a scene is scored only when a reader that fits has read it: otherwise the scene is ``pending`` with the reason and
  the segment prints it (:func:`board_lines`); nothing is guessed from the judge alone.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import agree as AG
from . import lineage

ROOT = Path(__file__).resolve().parents[1]
#: the readers file: the newest dated addendum of ``chat-readers-45.yaml`` (each one an index row; the base file's
#: row stays for the record). Loaded only when its sha256 is a row of the marks index (:func:`load_readers`).
READERS_BASE = ROOT / "calibration" / "chat-readers-45.yaml"


def _latest_readers_file() -> Path:
    adds = sorted((ROOT / "calibration").glob("chat-readers-45-addendum-*.yaml"))
    return adds[-1] if adds else READERS_BASE


READERS_CONFIG = _latest_readers_file()
NOT_NAMED = "readers not named yet"
#: API aliases of a reader's priced engine label (the label is the reader's id everywhere: pins, stamps, matching)
ALIASES = {"deepseek-v4-flash": "deepseek-flash", "deepseek-chat": "deepseek-flash"}


def canonical_id(model_id: str | None) -> str | None:
    """The priced engine label of a reader model id (an API alias maps to it; anything else is itself)."""
    return ALIASES.get(str(model_id).lower(), model_id) if model_id else model_id
STORE_DIR = "readers45"

#: the eight 4.1 scenes -> (Chat segment, points). Points are the segment shares of SPEC 1.2 (RV 0.5 each of the 4
#: Stays-in-character points, CC/RD 0.5 each of 3.5 Remembers, WR 0.75 each of 1.5 Co-writes).
SCENES = {
    "RV1-three-at-the-table": ("character", 0.5),
    "RV2-skarren-hut": ("character", 0.5),
    "CC3-tea-hut": ("remembers", 0.5),
    "CC4-second-pitch": ("remembers", 0.5),
    "RD1-marisol": ("remembers", 0.5),
    "RD4-kettle-saddle": ("remembers", 0.5),
    "WR1-ninth-string": ("cowrites", 0.75),
    "WR2-red-thread-key": ("cowrites", 0.75),
}
SEGMENT_LABELS = {"character": "Stays in character", "remembers": "Remembers the conversation",
                  "cowrites": "Co-writes to a brief"}
SEGMENT_MAX = {"character": 4.0, "remembers": 3.5, "cowrites": 1.5}
JUDGE_FAMILY = "google"


class NoFittingReader(ValueError):
    """No configured reader is of another family than both the judge and the graded model."""


# ------------------------------------------------------------------ the score
def _rate(x) -> float | None:
    """One side's fact rate: a number, or a mapping/list of fact results (bool, credit 0-1, or
    {"credit"|"pass", "weight"}) read as the weighted mean credit."""
    if x is None:
        return None
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return float(x)
    vals = list(x.values()) if isinstance(x, dict) else list(x)
    num = den = 0.0
    for v in vals:
        w = 1.0
        if isinstance(v, dict):
            w = float(v.get("weight", 1.0))
            c = v.get("credit", v.get("pass"))
            v = 0.0 if c is None else c
        num += w * (1.0 if v is True else 0.0 if v is False else float(v))
        den += w
    return None if not den else num / den


def scene_score(judge_facts, reader_facts) -> float | None:
    """The scene's fact rate = the mean of the judge's and the second reader's fact rates (the draft marks'
    ``mean_of_two_readers``). Each argument is a rate (0-1) or a mapping of fact results; None when either side
    is missing (a scene one reader has not read is not scored)."""
    j, r = _rate(judge_facts), _rate(reader_facts)
    if j is None or r is None:
        return None
    return (j + r) / 2.0


# ------------------------------------------------------------------ readers (configured, not hard-coded)
@dataclass(frozen=True)
class ReaderSpec:
    id: str | None
    family: str | None
    hosted: bool = False
    provider: str = ""
    role: str = ""
    thinking: bool = True
    display: str = ""

    @property
    def key(self) -> str:
        return AG.reader_key(self.id or "", self.thinking)

    def stamp(self, prompt_sha: str | None) -> dict:
        return {"model": self.id, "family": self.family, "provider": self.provider, "hosted": self.hosted,
                "prompt_sha": prompt_sha}


def load_readers(path: Path | str | dict | None = None) -> dict:
    """{"judge_family", "pins", "local_only", "local": [ReaderSpec], "hosted": [ReaderSpec]} from the readers file
    (or a mapping, for tests). A file is read only when its sha256 is a row of the marks index (an edited copy is
    refused with ``marksindex45.NotIndexed``). A reader without a model id is kept but never fits (its role is
    named, the id is not yet). ``local_only`` = graded families only local readers may read."""
    if isinstance(path, dict):
        d = path
    else:
        import yaml
        from . import marksindex45
        p = Path(path or READERS_CONFIG)
        marksindex45.require(p)
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    d = d.get("chat_readers") or d

    def spec(e, hosted):
        fam = e.get("family") or (lineage.family(e.get("id")) if e.get("id") else None)
        return ReaderSpec(e.get("id") or None, fam, hosted=hosted or bool(e.get("hosted")),
                          provider=str(e.get("provider") or ""), role=str(e.get("role") or ""),
                          thinking=bool(e.get("thinking", True)), display=str(e.get("display") or ""))
    return {"judge_family": d.get("judge_family") or JUDGE_FAMILY,
            "local_only": [str(x) for x in d.get("local_only_families") or []],
            "pins": {str(k): str(v) for k, v in (d.get("pins") or {}).items()},
            "local": [spec(e, False) for e in d.get("local") or []],
            "hosted": [spec(e, True) for e in d.get("hosted") or []]}


def display_name(reader_id: str | None, readers: dict | None = None) -> str:
    """The visitor-facing name of a reader id (the readers file's ``display``), else the id itself."""
    try:
        cfg = readers if readers is not None else load_readers()
    except Exception:          # noqa: BLE001 -- a refused file names nothing
        return str(reader_id or "")
    for r in list(cfg.get("local") or []) + list(cfg.get("hosted") or []):
        if r.id == reader_id and r.display:
            return r.display
    return str(reader_id or "")


def check_fit(reader: ReaderSpec, graded_family: str | None, judge_family: str = JUDGE_FAMILY) -> str | None:
    """None when ``reader`` may read a row of ``graded_family``; else why not. Enforced for every reading."""
    if not reader.id:
        return f"reader {reader.role or '?'} has no model id"
    if not reader.thinking:
        return f"reader {reader.id} is configured with thinking off (readers think)"
    if not reader.family:
        return f"reader {reader.id} has no known family"
    lin = lineage.family(reader.id)
    if lin and lin != reader.family:                    # a declared family never overrides what the id says
        return f"reader {reader.id} is declared {reader.family} but its lineage is {lin}"
    if reader.family == judge_family:
        return f"reader {reader.id} is of the judge's family ({judge_family})"
    if not graded_family:
        return "graded model family unknown"
    if reader.family == graded_family:
        return f"reader {reader.id} is of the graded model's family ({graded_family})"
    return None


def pick_reader(graded_family: str | None, readers: dict, allow_hosted: bool = True,
                unavailable: set | frozenset = frozenset()) -> ReaderSpec:
    """The first configured reader that fits: local readers in file order, then (when allowed) hosted ones.
    ``unavailable`` = reader ids to skip (failed their constructed-set mark, rig out). Raises
    :class:`NoFittingReader` naming each rejection."""
    why = []
    local_only = graded_family in set(readers.get("local_only") or ())
    pool = list(readers.get("local") or []) + (list(readers.get("hosted") or [])
                                                if allow_hosted and not local_only else [])
    if local_only and not any(r.id for r in readers.get("local") or []):
        raise NoFittingReader(f"{NOT_NAMED}: a {graded_family} row is read by local readers only and none is named")
    for r in pool:
        if r.id in unavailable:
            why.append(f"{r.id}: unavailable")
            continue
        bad = check_fit(r, graded_family, readers.get("judge_family") or JUDGE_FAMILY)
        if bad is None:
            return r
        why.append(bad)
    raise NoFittingReader(f"no reader fits a {graded_family or 'unknown-family'} model: " + "; ".join(why or ["none configured"]))


def pinned_reader(graded_family: str | None, readers: dict, allow_hosted: bool = True,
                  unavailable: set | frozenset = frozenset()) -> ReaderSpec:
    """THE reader of a graded family: the one ``pins:`` names in the readers file (it must fit), else the first
    fitting reader in file order (:func:`pick_reader`). One reader per family for every model of that family, so a
    scene is never read by whichever reader happened to run last. Raises :class:`NoFittingReader`."""
    pin = (readers.get("pins") or {}).get(graded_family or "")
    if pin:
        pool = list(readers.get("local") or []) + list(readers.get("hosted") or [])
        r = next((x for x in pool if x.id == pin), None)
        if r is None:
            raise NoFittingReader(f"pinned reader {pin} for {graded_family} is not in the readers file")
        if r.id in unavailable:
            raise NoFittingReader(f"pinned reader {pin} is unavailable")
        if r.hosted and graded_family in set(readers.get("local_only") or ()):
            raise NoFittingReader(f"pinned reader {pin} is hosted; a {graded_family} row is read by local readers only")
        bad = check_fit(r, graded_family, readers.get("judge_family") or JUDGE_FAMILY)
        if bad:
            raise NoFittingReader(bad)
        return r
    return pick_reader(graded_family, readers, allow_hosted, unavailable)


# ------------------------------------------------------------------ the reader store
def store_path(results: Path | str, label: str) -> Path:
    return Path(results) / STORE_DIR / f"{AG.slug(label)}.jsonl"


def prompt_sha_of(row: dict) -> str | None:
    """The judge prompt a stored row was read under (its rubric sha): the reader runs the same prompts."""
    return (((row.get("judge") or {}).get("instrument")) or {}).get("rubric_sha")


def load_store(results: Path | str, label: str) -> dict:
    """{(unit, reader key, prompt_sha, row_sha): record} (a later line replaces an earlier one)."""
    p = store_path(results, label)
    out = {}
    if p.exists():
        for line in p.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                if "vote" in r:                 # a classing vote (classing45) shares the model's file
                    continue
                out[(r["unit"], r["reader"], (r.get("reader_stamp") or {}).get("prompt_sha"), r.get("row_sha"))] = r
    return out


def append_store(results: Path | str, label: str, recs: list[dict]) -> None:
    p = store_path(results, label)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def run_second_reader(rows: list[dict], reader: ReaderSpec, *, jc, results: Path | str, label: str,
                      cases: dict | None = None, graded_family: str | None = None,
                      judge_family: str = JUDGE_FAMILY) -> list[dict]:
    """Read every judged long scene of one model's ``rows`` with ``reader`` (a JudgeClient-like ``jc`` with
    ``chat``), through the production prompts (agree.regrade_long, thinking on). A unit whose store line exists
    (same unit, reader, prompt, row) is never read again. Returns the records of this model for this reader
    (stored + new). Raises :class:`NoFittingReader` before any call when the reader does not fit this model, and
    ``judge45.HostedGrader`` for a hosted reader (owner 2026-11-21: no hosted grader or checker)."""
    if reader.hosted:
        from .judge45 import HostedGrader
        raise HostedGrader(f"Chat long-scene second reader: {reader.id} is hosted; MakoBench 4.5 grades with one "
                           "local judge only (no hosted grader or checker). Nothing was called.")
    units = AG.long_units(rows)
    gf = graded_family or AG.graded_family(label, next(iter(units.values()), None))
    bad = check_fit(reader, gf, judge_family)
    if bad:
        raise NoFittingReader(bad)
    cases = AG._case_map() if cases is None else cases
    have = load_store(results, label)
    out, new = [], []
    for uid, row in sorted(units.items()):
        psha = prompt_sha_of(row)
        k = (uid, reader.key, psha, AG.row_sha(row))
        if k in have:
            out.append(have[k])
            continue
        v = AG.regrade_long(jc, row, reader.thinking)
        if v.get("judge_failed") or not (v.get("elements") or {}).get("results"):
            continue                                    # unread: stays pending, retried on the next run
        rec = AG.long_record(label, uid, row, v, cases.get(row["case_id"]), reader.key)
        rec["graded_family"] = gf
        rec["reader_stamp"] = reader.stamp(psha)
        new.append(rec)
        out.append(rec)
    if new:
        append_store(results, label, new)
    return out


def import_agree_records(folder: Path | str, reader: ReaderSpec, stored_id: str | None = None,
                         rows: list[dict] | None = None) -> list[dict]:
    """The records an earlier ``crucibleforge agree`` run left in ``<folder>/agree/`` for this reader, stamped
    for the store format (no model call). ``stored_id`` = the id that run used for the same reader (an engine
    alias, e.g. ``deepseek-v4-flash`` for ``deepseek-flash``); it must be of the reader's family, and the stamp
    names the reader's own id (the priced engine label), so the records match the pin."""
    rd = reader
    if stored_id:
        fam = lineage.family(stored_id)
        if fam != reader.family:
            raise NoFittingReader(f"stored reader {stored_id} is {fam}, not {reader.family}")
        rd = ReaderSpec(id=stored_id, family=fam, hosted=reader.hosted, provider=reader.provider, role=reader.role,
                        thinking=reader.thinking)
    units = AG.long_units(rows) if rows else {}

    def stamp(r):
        if r.get("reader_stamp"):
            st = dict(r["reader_stamp"])
            if canonical_id(st.get("model")) == reader.id:
                st["model"] = reader.id
            return st
        row = units.get(r.get("unit"))
        # the prompt sha the unit's row was read under: a record counts only against its row's current rubric
        sha = prompt_sha_of(row) if row is not None and r.get("row_sha") in (None, AG.row_sha(row)) else None
        return reader.stamp(sha)          # the canonical id, whatever alias the agree run used
    return [{**r, "reader_stamp": stamp(r)}
            for r in AG.load_records(Path(folder), rd.key) if r.get("part") == AG.LONG_PART]


# ------------------------------------------------------------------ scoring rows
def scene_scores(rows: list[dict], records: list[dict], *, label: str, cases: dict | None = None,
                 graded_family: str | None = None, judge_family: str = JUDGE_FAMILY,
                 reader_for: ReaderSpec | None = None, readers: dict | None = None) -> dict:
    """{unit id: {case, segment, judge, reader, mean, status, reason, reader_stamp}} for every judged long scene
    of one model. The reader is the ONE pinned for the graded family (``reader_for``, else
    :func:`pinned_reader` over ``readers`` / the readers file). A record counts only when its model id and
    family equal that reader's and its prompt sha equals the row's current rubric sha; a record of any other
    reader, or of an older rubric, is ignored (never "newest wins"). status is ``ok`` or ``pending``."""
    cases = AG._case_map() if cases is None else cases
    units = AG.long_units(rows)
    gf = graded_family or AG.graded_family(label, next(iter(units.values()), None))
    pin, pin_why = reader_for, None
    if pin is not None:
        pin_why = check_fit(pin, gf, judge_family)
        pin = None if pin_why else pin
    else:
        try:
            pin = pinned_reader(gf, readers if readers is not None else load_readers(), True)
        except NoFittingReader as e:
            pin_why = str(e)
    by_unit: dict[str, list[dict]] = {}
    for r in records:
        if r.get("part", AG.LONG_PART) == AG.LONG_PART and r.get("model", label) == label:
            by_unit.setdefault(r["unit"], []).append(r)
    out = {}
    for uid, row in sorted(units.items()):
        case = row["case_id"]
        seg = SCENES.get(case, (None, 0.0))[0]
        jrate = AG.long_score(row, row.get("judge") or {}, cases.get(case))
        cur = prompt_sha_of(row)
        cand = [r for r in by_unit.get(uid, []) if r.get("row_sha") in (None, AG.row_sha(row))]
        good = None
        if pin is None:
            why = pin_why or "no reader named for this model's family"
        else:
            mine = [r for r in cand
                    if canonical_id((r.get("reader_stamp") or {}).get("model")) == pin.id
                    and (r.get("reader_stamp") or {}).get("family") == pin.family
                    and cur and (r.get("reader_stamp") or {}).get("prompt_sha") == cur
                    and r.get("score_reader") is not None]
            if mine:
                good, why = mine[-1], None
            elif cand:
                why = f"no record from the pinned reader {pin.id} under this rubric"
            else:
                why = "not read by a second reader"
        entry = {"case": case, "segment": seg, "judge": jrate, "reader": None, "mean": None, "status": "pending",
                 "reason": why, "reader_stamp": None}
        if good is not None and jrate is not None:
            entry.update(reader=good["score_reader"], mean=scene_score(jrate, good["score_reader"]), status="ok",
                         reason=None, reader_stamp=good.get("reader_stamp"))
        elif jrate is None:
            entry["reason"] = "judge verdict not scorable"
        out[uid] = entry
    return out


#: 11b (lead ruling 2026-11-21): Chat is graded by the one judge alone; the profile says ``rplong_score.definition:
#: judge_only``. Stored second-reader votes (hosted ones included) are never read for a score.
JUDGE_ONLY = "judge_only"
NOT_JUDGED = "not judged yet"


def judge_only(profile: dict | None) -> bool:
    """True when the profile scores the long scenes by the judge alone (4.5 since 2026-11-21)."""
    d = ((profile or {}).get("rplong_score") or {}).get("definition")
    return d is None or str(d) == JUDGE_ONLY


def judge_scores(rows: list[dict], records=None, *, label: str, cases: dict | None = None, units=None) -> dict:
    """{unit id: {case, segment, judge, reader: None, mean, status, reason}} of the judged long scenes with the
    judge's fact rate as the scene's score. ``records`` (any reader store) is accepted and ignored. A scene with no
    scorable judge verdict is pending "not judged yet"."""
    cases = AG._case_map() if cases is None else cases
    units = AG.long_units(rows) if units is None else units
    out = {}
    for uid, row in sorted(units.items()):
        case = row["case_id"]
        j = AG.long_score(row, row.get("judge") or {}, cases.get(case))
        j = _rate(j)
        out[uid] = {"case": case, "segment": SCENES.get(case, (None, 0.0))[0], "judge": j, "reader": None,
                    "mean": j, "status": "ok" if j is not None else "pending",
                    "reason": None if j is not None else NOT_JUDGED, "reader_stamp": None}
    return out


def segment_points(scores: dict) -> dict:
    """Per segment: the points the eight scenes carry, from ``scene_scores`` output.
    {segment: {label, max, scene_points, points, covered, expected, missing, reasons, counted}}.
    ``points`` is the sum over scenes of (scene points x the scene's mean over its repeats); a segment whose scenes
    are not all scored is not counted (``counted`` False, ``points`` None): the profile lane must not print a
    number for it."""
    per_case: dict[str, list[float]] = {}
    reasons: dict[str, str] = {}
    for u in scores.values():
        if u["status"] == "ok":
            per_case.setdefault(u["case"], []).append(u["mean"])
        else:
            reasons.setdefault(u["case"], u["reason"])
    out = {}
    for seg, lab in SEGMENT_LABELS.items():
        cases = [c for c, (s, _) in SCENES.items() if s == seg]
        got = [c for c in cases if per_case.get(c)]
        # a scene with a pending repeat is not fully read
        pend = [c for c in cases if c in reasons or c not in per_case]
        miss = sorted(set(pend))
        pts = sum(SCENES[c][1] * (sum(per_case[c]) / len(per_case[c])) for c in got)
        out[seg] = {"label": lab, "max": SEGMENT_MAX[seg], "scene_points": sum(SCENES[c][1] for c in cases),
                    "points": None if miss else pts, "partial_points": pts, "covered": len(cases) - len(miss),
                    "expected": len(cases), "missing": miss,
                    "reasons": {c: reasons.get(c, "scene not run") for c in miss}, "counted": not miss}
    return out


def coverage(scores: dict) -> dict:
    """Per model coverage: {units, read, pending, by_reason, hosted, readers}."""
    n = len(scores)
    ok = [u for u in scores.values() if u["status"] == "ok"]
    by: dict[str, int] = {}
    for u in scores.values():
        if u["status"] != "ok":
            by[u["reason"]] = by.get(u["reason"], 0) + 1
    stamps = [u["reader_stamp"] or {} for u in ok]
    return {"units": n, "read": len(ok), "pending": n - len(ok), "by_reason": by,
            "hosted": any(s.get("hosted") for s in stamps),
            "readers": sorted({s.get("model") for s in stamps if s.get("model")})}


# ------------------------------------------------------------------ what the board prints
def board_lines(segments: dict, cov: dict | None = None, marks: dict | None = None) -> list[str]:
    """The lines a model's Chat segments print for the long scenes (SPEC 4.4 W2): a segment that cannot be
    scored says why; a failed or missing mark says so in numbers; a hosted reader is named."""
    out = []
    for seg, s in segments.items():
        if not s["counted"]:
            why = "; ".join(f"{c}: {r}" for c, r in sorted(s["reasons"].items()))
            out.append(f"{s['label']}: long scenes pending, {s['covered']} of {s['expected']} read ({why}).")
    if cov and cov.get("hosted"):
        out.append("Long scenes read by hosted reader.")
    if marks is not None:
        if not marks.get("measured", True):
            out.append("Long scenes pending: agreement with the second reader not measured.")
        elif not marks.get("pass"):
            out.append("Long scenes pending: " + (marks.get("reason") or "marks not passed") + ".")
    return out


def judge_term(diffs: list[float], readers: int = 2) -> float | None:
    """The judge term a part adds to its margin, as a share (0-1) of the scene score, from the unit differences
    d = judge - reader. One reader alone: agree.judge_sd. Mean of two readers: the noise and the leniency term
    each halve (error of a mean of two exchangeable readers), so the term is the single-reader one / sqrt(2)."""
    sd = AG.judge_sd(diffs)
    if sd is None:
        return None
    return sd / math.sqrt(2.0) if readers == 2 else sd
