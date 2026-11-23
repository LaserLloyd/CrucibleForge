#!/usr/bin/env python3
"""Apply the pre-written segment, Everyday and act-and-beat marks to a set of finished run folders.

  check_marks45.py --folders <run folder> [<run folder> ...] [--out-name marks45.json] [--dry-run]
                   [--no-refresh] [--no-write]

One folder = one run of one model (its ``report.json`` holds one model block). Known-order marks need several
models at once, ceiling and floor marks are per model, so give every folder of the release together.

Reads: ``calibration/segment-marks-45.json``, ``everyday-marks-45.json``, ``act-beat-marks-45.json`` (each refused
unless its sha256 is a row of ``calibration/MARKS-INDEX-45.md``; never edited here), and from each folder's
``report.json`` the ``marks_input45`` block (segment values before any mark holds them; written by ``crucibleforge
report``; a stale report is re-made once). Writes, into each folder's ``<out-name>`` (default ``marks45.json``), the
blocks ``segments``, ``everyday`` and ``act_beat`` that the board reads (read-modify-write, atomic: every other block
is kept). Each result: ``{id, status: pass|fail|not_evaluable, measured, threshold, reason, folders, marks_file,
marks_sha256, parts}``. A mark whose needed model or part is absent is ``not_evaluable`` and does not pass.

Exit 0: every mark evaluated and passed. 1: a mark failed. 2: none failed but a mark is not evaluable.
3: integrity (a mark file not indexed, a folder without a usable report).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from crucibleforge.marksinput45 import adult_key  # noqa: E402

CAL = ROOT / "calibration"
SEG_FILE, EV_FILE, AB_FILE = "segment-marks-45.json", "everyday-marks-45.json", "act-beat-marks-45.json"
#: how the marks are evaluated from stored data (margins, AB2's denominator, EV3's identity, anchors); identity and
#: evaluation only, no threshold
EVAL_FILE = "marks-evaluation-45-addendum-20261120.json"
PAIRS_FILE = "marks-pairs-45-addendum-20261120.json"
ASSIST_FILE = "assistant_marks_20261119.json"
PASS, FAIL, NE = "pass", "fail", "not_evaluable"
FIXTURE_TESTS = {"AB3": "tests/test_actkeys45_both_ways.py", "AB5": "tests/test_nsfw_plus_zero_taste.py"}
CEILING_AREAS = ("code/", "chat/", "nsfw/", "unc/")


class Integrity(Exception):
    pass


# ------------------------------------------------------------------ inputs
class Run:
    """One folder: its label and the report's mark inputs."""

    def __init__(self, folder: Path, label: str, mi: dict, anchor: bool = False):
        self.folder, self.label, self.mi, self.anchor = folder, label, mi, anchor

    def seg(self, key: str) -> dict | None:
        return (self.mi.get("segments") or {}).get(key)


def _refresh(folder: Path) -> None:
    exe = Path(sys.executable).parent / "crucibleforge"
    cmd = [str(exe) if exe.exists() else "crucibleforge", "--results", str(folder), "report"]
    subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)


def load_runs(folders: list[Path], refresh: bool = True, anchor: bool = False) -> list[Run]:
    runs: list[Run] = []
    for f in folders:
        rp = f / "report.json"
        for attempt in (0, 1):
            d = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else {}
            models = d.get("models") or {}
            if models and all("marks_input45" in m for m in models.values()):
                break
            if attempt == 0 and refresh and (f / "transcripts_x").parent.is_dir():
                _refresh(f)
        else:
            raise Integrity(f"{f}: report.json has no marks_input45 (run `crucibleforge report` on the folder)")
        if not models:
            raise Integrity(f"{f}: report.json holds no model")
        for label, m in models.items():
            mi = m["marks_input45"]
            if mi.get("error") and not mi.get("segments"):
                raise Integrity(f"{f}: marks_input45 error: {mi['error']}")
            runs.append(Run(f, label, mi, anchor))
    return runs


def _load_marks() -> dict:
    from crucibleforge import marksindex45
    out = {}
    for key, name in (("segments", SEG_FILE), ("everyday", EV_FILE), ("act_beat", AB_FILE), ("evaluation", EVAL_FILE),
                      ("pairs", PAIRS_FILE)):
        p = CAL / name
        try:
            sha = marksindex45.require(p)
        except (marksindex45.NotIndexed, OSError) as e:
            raise Integrity(str(e))
        out[key] = {"file": name, "sha": sha, "body": json.loads(p.read_text(encoding="utf-8"))}
    return out


# ------------------------------------------------------------------ models
def _patterns(spec: str) -> list[str]:
    return [p.strip().lower() for p in spec.split(" / ") if p.strip()]


def _norm_label(s: str) -> str:
    """A run label and a role pattern are compared with '.' and '_' read as '-' (marks-pairs addendum 2026-11-20:
    the engine writes ``minimax-m3-1-flash`` where the marks say ``minimax-m3.1-flash*``; identity only)."""
    return s.lower().replace(".", "-").replace("_", "-")


def match_labels(labels: list[str], spec: str) -> list[str]:
    pats = [_norm_label(p) for p in _patterns(spec)]
    return sorted({l for l in labels if any(fnmatch.fnmatchcase(_norm_label(l), p) for p in pats)})


class Models:
    """Role -> label resolution and per-model segment values over the runs."""

    def __init__(self, runs: list[Run], roles: dict[str, str]):
        self.runs, self.roles = runs, roles
        # anchors (validation-only models) are never a launch model: ceilings, pairs and role resolution read
        # ``labels`` (launch only); only the Everyday checks (EV1/EV2/EV3/EV5) look at ``anchor_labels`` as well
        self.labels = sorted({r.label for r in runs if not r.anchor})
        self.anchor_labels = sorted({r.label for r in runs if r.anchor} - set(self.labels))

    def role_labels(self, role: str, anchors: bool = False) -> list[str]:
        return match_labels(self.labels + (self.anchor_labels if anchors else []), self.roles[role])

    def role(self, role: str, anchors: bool = False) -> tuple[str | None, str | None]:
        """(label, problem): exactly one label per role. ``anchors``: a validation-only model may fill the role
        (the Everyday checks only)."""
        ls = self.role_labels(role, anchors)
        if not ls:
            return None, (f"{role} model not on the board: no folder holds a model matching "
                          f"{self.roles[role]}")
        if len(ls) > 1:
            return None, f"{role} is ambiguous: {', '.join(ls)}"
        return ls[0], None

    def runs_of(self, label: str) -> list[Run]:
        return [r for r in self.runs if r.label == label]

    def carrying(self, label: str, key: str) -> tuple[list[Run], list[Run]]:
        """(runs whose value counts, runs missing the key that are not skipped). Only the adult page's keys
        (``marksinput45.adult_key``: that page ran once per model) may be missing from a run; a run missing any
        other key (Code, Chat) leaves the model's value None (review S3)."""
        runs = self.runs_of(label)
        carry = [r for r in runs if r.seg(key)]
        lacking = [] if adult_key(key) else [r for r in runs if not r.seg(key)]
        return carry, lacking

    def value(self, label: str, key: str) -> dict | None:
        """{points, margin, max, n, share, margin_known, runs_used, runs_total, runs_note} over the label's runs
        that CARRY the segment key, or None. Only an adult-page key may be missing from a run (the adult page ran
        once per model, so a model's other runs hold no nsfw/unc keys at all); a run missing a Code or Chat key, or
        a run that has the key but no complete value, gives None; no run carrying it gives None (``why_missing``
        says so). Several runs: mean of points; margin = root mean square of the runs' margins (no gain from
        replication is claimed)."""
        pts, mgs, mx, known = [], [], None, True
        runs = self.runs_of(label)
        carry, lacking = self.carrying(label, key)
        if lacking:
            return None
        for r in carry:
            s = r.seg(key)
            if s.get("points") is None:
                return None
            pts.append(float(s["points"]))
            mx = float(s["max"])
            if s.get("margin") is None:        # the stored 90% margin (segments45 / nsfwplus45); none = unknown
                known = False
            mgs.append(float(s.get("margin") or 0.0))
        if not pts:
            return None
        return {"points": sum(pts) / len(pts), "margin": math.sqrt(sum(m * m for m in mgs) / len(mgs)), "max": mx,
                "n": len(pts), "share": sum(pts) / len(pts) / mx if mx else None, "margin_known": known,
                "run_shares": [p / mx for p in pts], "runs_used": len(carry), "runs_total": len(runs),
                "runs_note": f"{len(carry)} of {len(runs)} runs carry this segment"}

    def why_missing(self, label: str, key: str) -> str:
        runs = self.runs_of(label)
        carry, lacking = self.carrying(label, key)
        if runs and not carry:
            return f"{label}: no run carries {key} (0 of {len(runs)} runs)"
        if lacking:
            return (f"{label}: {key} missing from {len(lacking)} of {len(runs)} runs ("
                    + ", ".join(sorted({r.folder.name for r in lacking})) + "): only the adult page's keys may be "
                    "missing from a run")
        for r in carry:
            s = r.seg(key)
            if s.get("points") is None:
                return f"{label}: {key} pending ({s.get('reason') or 'no value'})"
        return f"{label}: no run"


def _res(mid: str, status: str, measured, threshold, reason: str, folders, marks: dict, parts=None) -> dict:
    return {"id": mid, "status": status, "measured": measured, "threshold": threshold, "reason": reason,
            "folders": [str(f) for f in folders], "marks_file": marks["file"], "marks_sha256": marks["sha"],
            **({"parts": parts} if parts else {})}


def _part(name, status, measured, threshold, reason="") -> dict:
    return {"name": name, "status": status, "measured": measured, "threshold": threshold, "reason": reason}


def _combine(parts: list[dict], required: list[str]) -> tuple[str, str]:
    bad = [p for p in parts if p["status"] == FAIL]
    if bad:
        return FAIL, "; ".join(f"{p['name']}: {p['reason'] or 'not met'}" for p in bad)
    ne = [p for p in parts if p["status"] == NE and p["name"] in required]
    if ne:
        return NE, "; ".join(f"{p['name']}: {p['reason']}" for p in ne)
    return PASS, ""


# ------------------------------------------------------------------ segment marks
def pair_part(M: Models, key: str, spec: list, name: str, primary: bool) -> dict:
    a_role, op, b_role = spec
    thr = f"{a_role} {op} {b_role}, or inside the combined margin"
    la, pa = M.role(a_role)
    lb, pb = M.role(b_role)
    if pa or pb:
        return _part(name, NE, None, thr, "not tested: " + "; ".join(x for x in (pa, pb) if x))
    va, vb = M.value(la, key), M.value(lb, key)
    if va is None or vb is None:
        why = M.why_missing(la, key) if va is None else M.why_missing(lb, key)
        return _part(name, NE, None, thr, "not tested: " + why)
    comb = math.sqrt(va["margin"] ** 2 + vb["margin"] ** 2)
    diff = va["points"] - vb["points"]
    known = va["margin_known"] and vb["margin_known"]
    meas = {"a": la, "b": lb, "a_points": round(va["points"], 4), "b_points": round(vb["points"], 4),
            "diff": round(diff, 4), "combined_margin": round(comb, 4) if known else None,
            "a_runs": va["runs_note"], "b_runs": vb["runs_note"]}
    if diff > 0:
        return _part(name, PASS, {**meas, "state": "holds"}, thr)
    if not known:
        # a reversal (or a tie) is judged against the real combined margin only; none stored = not evaluable
        return _part(name, NE, meas, thr, "no 90% margin is stored for this segment of "
                     + ", ".join(l for l, v in ((la, va), (lb, vb)) if not v["margin_known"]))
    if -diff > comb + 1e-12:
        return _part(name, FAIL, meas, thr, f"order reversed: {la} {va['points']:.3f} vs {lb} {vb['points']:.3f} "
                     f"(margin {comb:.3f})")
    return _part(name, PASS, {**meas, "state": "not separated"}, thr)


def ceiling_part(M: Models, key: str, seg_max: float, ref_role: str = "reference") -> dict:
    thr = "at least one measured model with points + margin < max"
    rows, unknown = [], []
    for lab in M.labels:
        v = M.value(lab, key)
        if v is None:
            continue
        if not v["margin_known"]:
            unknown.append(lab)
        rows.append((lab, v))
    if not rows:
        why = "; ".join(sorted({M.why_missing(l, key) for l in M.labels})[:3])
        return _part("ceiling", NE, None, thr, why or "no model on the board")
    if unknown:
        return _part("ceiling", NE, {"models": len(rows)}, thr,
                     "no 90% margin is stored for this segment, so 'within its own margin of the maximum' "
                     "cannot be tested")
    under = [(l, v["points"] + v["margin"]) for l, v in rows if v["points"] + v["margin"] < seg_max - 1e-12]
    best = min(rows, key=lambda lv: lv[1]["points"] + lv[1]["margin"])
    meas = {"models": len(rows), "passing": [l for l, _ in under], "lowest_upper": round(
        best[1]["points"] + best[1]["margin"], 4), "max": seg_max}
    rl, _ = M.role(ref_role)
    rv = M.value(rl, key) if rl else None
    if rv:
        meas["reference_share"] = round(rv["share"], 4)
    if under:
        return _part("ceiling", PASS, meas, thr)
    return _part("ceiling", FAIL, meas, thr, f"every measured model is within its margin of the maximum {seg_max:g}")


def share_marks(M: Models, ev1_min: float, gap_min: float):
    """(EV1 part, EV2 part, ceiling-pass role info) helpers for Everyday."""
    key = "chat/everyday"
    rl, p = M.role("reference")
    if p:
        ev1 = _part("reference_share", NE, None, f">= {ev1_min} on each of two runs", p)
    else:
        runs = M.runs_of(rl)
        shares = []
        for r in runs:
            s = r.seg(key)
            shares.append(None if not s or s.get("points") is None else s["points"] / s["max"])
        if len(runs) < 2:
            ev1 = _part("reference_share", NE, {"runs": len(runs)}, f">= {ev1_min} on each of two runs",
                        f"the reference has {len(runs)} run(s); the mark needs its two runs")
        elif any(x is None for x in shares):
            ev1 = _part("reference_share", NE, None, f">= {ev1_min} on each of two runs", M.why_missing(rl, key))
        else:
            ok = min(shares) >= ev1_min - 1e-12
            ev1 = _part("reference_share", PASS if ok else FAIL, {"shares": [round(x, 4) for x in shares]},
                        f">= {ev1_min} on each of two runs",
                        "" if ok else f"lowest run {min(shares):.3f}")
    dl, pd = M.role("damaged", anchors=True)
    if p or pd:
        ev2 = _part("damaged_gap", NE, None, f"damaged >= {gap_min} of the segment below the reference",
                    "; ".join(x for x in (p, pd) if x))
    else:
        rv, dv = M.value(rl, key), M.value(dl, key)
        if rv is None or dv is None:
            ev2 = _part("damaged_gap", NE, None, f"damaged >= {gap_min} of the segment below the reference",
                        M.why_missing(rl, key) if rv is None else M.why_missing(dl, key))
        else:
            gap = rv["share"] - dv["share"]
            ok = gap >= gap_min - 1e-12
            ev2 = _part("damaged_gap", PASS if ok else FAIL,
                        {"reference": round(rv["share"], 4), "damaged": round(dv["share"], 4), "gap": round(gap, 4)},
                        f"damaged >= {gap_min} of the segment below the reference", "" if ok else f"gap {gap:.3f}")
    return ev1, ev2


def _required_pair(M: Models, e: dict, pairs: dict | None):
    """The pair that must be tested for a segment (marks-pairs addendum 2026-11-20).

    The primary pair as written, unless it names a role the addendum lists as unavailable AND no run of that role
    is on the board: then the first secondary pair whose two roles are both available. Returns
    ``(required_spec, as_written_reason, index_of_the_secondary_used)``; the last two are None when the written
    primary stands."""
    gone = (pairs or {}).get("unavailable_roles") or {}
    prim = e["primary"]
    missing = [r for r in (prim[0], prim[2]) if r in gone and M.role(r)[1]]
    if not missing:
        return prim, None, None
    why = "; ".join(f"{r}: {gone[r]}" for r in missing)
    for i, sec in enumerate(e.get("secondary") or []):
        if not any(r in gone and M.role(r)[1] for r in (sec[0], sec[2])):
            return sec, why, i
    return prim, None, None


def segment_marks(M: Models, mk: dict, folders, pairs: dict | None = None) -> dict:
    body = mk["body"]
    out = {}
    extra_by = {}
    for key, e in body["segments"].items():
        parts = []
        if key.startswith(CEILING_AREAS):
            parts.append(ceiling_part(M, key, float(e["max"])))
        req, why_written, used = _required_pair(M, e, pairs)
        if why_written:
            a, op, b = e["primary"]
            parts.append(_part("written_primary", NE, None, f"{a} {op} {b}, or inside the combined margin",
                               "not tested: " + why_written))
        parts.append(pair_part(M, key, req, "primary", True))
        for i, sec in enumerate(e.get("secondary") or []):
            if i == used:
                continue
            parts.append(pair_part(M, key, sec, f"secondary{i + 1}", False))
        if e.get("extra"):
            ex = e["extra"]
            ev1, ev2 = share_marks(M, float(ex["reference_min_share"]), float(ex["damaged_gap_min_share"]))
            parts += [ev1, ev2]
        required = [p["name"] for p in parts if not p["name"].startswith(("secondary", "written_primary"))]
        status, why = _combine(parts, required)
        measured = {p["name"]: p["measured"] for p in parts}
        thr = {p["name"]: p["threshold"] for p in parts}
        out[key] = _res(f"segment:{key}", status, measured, thr, why, folders, mk, parts)
    return out


# ------------------------------------------------------------------ everyday marks
def _assistant(folders: list[Path], pair_roles: dict | None = None) -> tuple[str, dict, str]:
    try:
        import check_assistant_marks as CA
        from crucibleforge import assistant45, profiles
        marks = json.loads((CAL / ASSIST_FILE).read_text(encoding="utf-8"))
        from crucibleforge import marksindex45
        marksindex45.require(CAL / ASSIST_FILE)
        prof = profiles.load_profile("bench45")
        if assistant45.block(prof) is None:
            return NE, {}, "the profile has no assistant block"
        runs = CA.collect(list(folders), prof)
        roles = {k: list(v) for k, v in marks["set"].items()}
        # one identity per role (marks-evaluation addendum 2026-11-20): the reference and the damaged stand-in are the
        # models the segment marks name (segment-marks-45.json pair_rule.models, or --reference); identity only
        for r_ in ("reference", "damaged"):
            if (pair_roles or {}).get(r_):
                roles[r_] = _patterns(pair_roles[r_])
        ok, lines = CA.check(runs, marks, roles)
    except Exception as e:                           # noqa: BLE001 -- unreadable inputs are not a pass
        return NE, {}, f"assistant marks could not be evaluated ({type(e).__name__}: {e})"
    miss = [l for l in lines if l.startswith("FAIL") and ("not run:" in l or "no measured score" in l
                                                         or "measured runs" in l)]
    meas = {"lines": [l for l in lines if l.startswith(("PASS", "FAIL"))]}
    if miss:
        return NE, meas, miss[0][4:].strip()
    return (PASS if ok else FAIL), meas, "" if ok else next((l[4:].strip() for l in lines if l.startswith("FAIL")), "")


def _synthetic_know(levels=(3, 5, 6, 7), seeds=8, cap=0.5) -> tuple[bool, float]:
    from crucibleforge.gen import knowgap as kg
    worst = 0.0
    for lv in levels:
        for i in range(seeds):
            m = kg.materialize({"type": "knowgap", "level": lv}, release_seed="marks45", case_id=f"EV4-{lv}-{i}")
            ans = kg.strategy(m["grader_config"], "decline")
            worst = max(worst, kg.grade(ans, m))
    return worst <= cap + 1e-12, worst


def tests_page_says_floor() -> bool:
    for name in ("WHAT-IS-TESTED.md", "SCORING.md"):
        p = ROOT / "docs" / name
        if p.is_file() and re.search(r"Everyday[^\n]*\bfloor\b", p.read_text(encoding="utf-8"), re.I):
            return True
    return False


def everyday_marks(M: Models, mk: dict, folders, segmarks: dict, anchors=()) -> dict:
    body = mk["body"]["marks"]
    out: dict = {}
    ev1_min = float(re.search(r">=\s*([0-9.]+)", body["EV1_reference"]).group(1))
    gap_min = float(re.search(r">=\s*([0-9.]+)", body["EV2_damaged_gap"]).group(1))
    ev1, ev2 = share_marks(M, ev1_min, gap_min)
    for part, name in ((ev1, "EV1_reference"), (ev2, "EV2_damaged_gap")):
        out[name] = _res(f"everyday:{name[:3]}", part["status"], part["measured"], part["threshold"],
                         part["reason"], folders, mk)
    st, meas, why = _assistant(list(folders) + list(anchors), M.roles)
    out["EV3_basics_marks"] = _res("everyday:EV3", st, meas, "calibration/assistant_marks_20261119.json passes",
                                   why, folders, mk)
    # EV4
    rl, p = M.role("reference")
    thr4 = "every pair's answerable member answered by the reference on both runs; all-NOT-STATED <= 0.5 of Know"
    if p:
        out["EV4_know_contrast"] = _res("everyday:EV4", NE, None, thr4, p, folders, mk)
    else:
        runs = M.runs_of(rl)
        ks = [r.mi.get("know") for r in runs]
        if len(runs) < 2 or any(k is None for k in ks):
            out["EV4_know_contrast"] = _res("everyday:EV4", NE, {"runs": len(runs)}, thr4,
                                            "the reference needs two runs that hold KNOWGAP rows", folders, mk)
        else:
            full = all(k["pairs"] > 0 and k["value_ok"] == k["pairs"] for k in ks)
            ok_b, worst = _synthetic_know()
            meas = {"value_sides": [f"{k['value_ok']}/{k['pairs']}" for k in ks], "all_decline_share": round(worst, 4)}
            bad = []
            if not full:
                bad.append("a reference run left an answerable pair unanswered")
            if not ok_b:
                bad.append(f"all-NOT-STATED scores {worst:.2f} of Know")
            out["EV4_know_contrast"] = _res("everyday:EV4", FAIL if bad else PASS, meas, thr4, "; ".join(bad),
                                            folders, mk)
    # EV5
    key = "chat/everyday"
    thr5 = "ceiling passes only through damaged or small models; the tests page says the segment is a floor"
    models = [(l, M.value(l, key)) for l in M.labels + M.anchor_labels]      # EV5 reads the anchors too
    models = [(l, v) for l, v in models if v]
    if not models:
        out["EV5_floor_disclosure"] = _res("everyday:EV5", NE, None, thr5, "no model has a complete Everyday value",
                                           folders, mk)
    else:
        marks = json.loads((CAL / ASSIST_FILE).read_text(encoding="utf-8"))
        weak = set(match_labels([l for l, _ in models], M.roles["damaged"])) | \
            set(match_labels([l for l, _ in models], " / ".join(marks["set"]["small"])))
        passing = [l for l, v in models if v["points"] + v["margin"] < v["max"] - 1e-12]
        says = tests_page_says_floor()
        bad, unknown = [], []
        if not passing:
            if weak:
                bad.append("no model passes the ceiling, not even a damaged or small one")
            else:
                unknown.append("no damaged or small model is on the board, so the ceiling cannot pass through one "
                               f"(damaged: {M.roles['damaged']}; small: {' / '.join(marks['set']['small'])})")
        elif any(l not in weak for l in passing):
            bad.append("a sound model passes the ceiling: " + ", ".join(l for l in passing if l not in weak))
        if not says:
            bad.append("the tests page does not say the segment is a floor")
        out["EV5_floor_disclosure"] = _res("everyday:EV5", FAIL if bad else NE if unknown else PASS,
                                           {"passing": passing, "tests_page_says_floor": says}, thr5,
                                           "; ".join(bad or unknown), folders, mk)
    return out


# ------------------------------------------------------------------ act-and-beat marks
def _units(runs: list[Run]) -> list[dict]:
    return [u for r in runs for u in (r.mi.get("ab_units") or [])]


def _pytest(rel: str) -> tuple[bool, str]:
    p = ROOT / rel
    if not p.is_file():
        return False, f"{rel} is missing"
    tmp = tempfile.mkdtemp(prefix="marks45-pt-", dir=os.environ.get("MARKS45_TMP", "/var/tmp"))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp",
                            str(Path(tmp) / "bt"), str(p)], cwd=ROOT, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    return r.returncode == 0, tail


def actbeat_marks(runs: list[Run], mk: dict, folders, pytest_runner=_pytest) -> dict:
    marks = mk["body"]["marks"]
    units = _units(runs)
    out: dict = {}
    keyed = [u for u in units if u.get("act_key") and u.get("act_present") is not None]
    # AB1
    m1 = marks["AB1_explicit_wrongly_zeroed"]
    thr = f"<= {m1['max_pooled']:.0%} pooled (>= 100 EXPLICIT replies), <= {m1['max_per_card']:.0%} on a card with >= 4"
    expl = [u for u in keyed if u["cls"] == "EXPLICIT"]
    if len(expl) < 100:
        out["AB1_explicit_wrongly_zeroed"] = _res("act_beat:AB1", NE, {"explicit_replies": len(expl)}, thr,
                                                  f"{len(expl)} EXPLICIT-classed replies; the mark needs >= 100",
                                                  folders, mk)
    else:
        z = sum(1 for u in expl if not u["act_present"])
        rate = z / len(expl)
        cards: dict = {}
        for u in expl:
            c = cards.setdefault(u["card"], [0, 0])
            c[0] += 1
            c[1] += (not u["act_present"])
        badc = {c: round(b / n, 3) for c, (n, b) in cards.items() if n >= 4 and b / n > m1["max_per_card"] + 1e-12}
        why = []
        if rate > m1["max_pooled"] + 1e-12:
            why.append(f"pooled {z}/{len(expl)} = {rate:.1%}")
        if badc:
            why.append("cards over the per-card limit: " + ", ".join(f"{c} {r:.0%}" for c, r in sorted(badc.items())))
        out["AB1_explicit_wrongly_zeroed"] = _res("act_beat:AB1", FAIL if why else PASS,
                                                  {"pooled": f"{z}/{len(expl)}", "rate": round(rate, 4),
                                                   "cards_over": badc}, thr, "; ".join(why), folders, mk)
    # AB2
    m2 = marks["AB2_backstop"]
    soft = [u for u in keyed if u["cls"] == "SOFTENED"]
    z2 = sum(1 for u in soft if not u["act_present"])
    thr2 = (f">= {m2['min']:.0%} of replies classed SOFTENED on cards with an act key (all SOFTENED replies: the votes "
            "store no finer reason; marks-evaluation addendum 2026-11-20)")
    if not soft:
        out["AB2_backstop"] = _res("act_beat:AB2", NE, {"softened_replies": 0}, thr2,
                                   "no reply on a card with an act key is classed SOFTENED", folders, mk)
    else:
        rate2 = z2 / len(soft)
        ok2 = rate2 >= m2["min"] - 1e-12
        out["AB2_backstop"] = _res("act_beat:AB2", PASS if ok2 else FAIL,
                                   {"softened_replies": len(soft), "act_check_zeroes": z2, "rate": round(rate2, 4)},
                                   thr2, "" if ok2 else f"{z2}/{len(soft)} = {rate2:.1%}", folders, mk)
    # AB3 / AB5 (fixture tests)
    for name, tid in (("AB3_both_ways", "AB3"), ("AB5_no_taste", "AB5")):
        ok, tail = pytest_runner(FIXTURE_TESTS[tid])
        out[name] = _res(f"act_beat:{tid}", PASS if ok else FAIL, {"tests": FIXTURE_TESTS[tid], "result": tail},
                         "the mark's fixture tests pass", "" if ok else tail, folders, mk)
    # AB4
    thr4 = "mean brief share higher for EXPLICIT than SOFTENED by >= 0.20 on writing cards"
    wr = [u for u in units if u.get("brief") is not None]
    e = [u["brief"] for u in wr if u["cls"] == "EXPLICIT"]
    s = [u["brief"] for u in wr if u["cls"] == "SOFTENED"]
    if not e or not s:
        out["AB4_beats_separate"] = _res("act_beat:AB4", NE, {"explicit": len(e), "softened": len(s)}, thr4,
                                         "needs writing-card replies of both classes", folders, mk)
    else:
        gap = sum(e) / len(e) - sum(s) / len(s)
        out["AB4_beats_separate"] = _res("act_beat:AB4", PASS if gap >= 0.20 - 1e-12 else FAIL,
                                         {"explicit_mean": round(sum(e) / len(e), 4), "softened_mean":
                                          round(sum(s) / len(s), 4), "gap": round(gap, 4), "n": [len(e), len(s)]},
                                         thr4, "" if gap >= 0.20 - 1e-12 else f"gap {gap:.3f}", folders, mk)
    return out


# ------------------------------------------------------------------ writing
def _atomic_write(path: Path, body: dict) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(body, indent=1, default=list), encoding="utf-8")
    os.replace(tmp, path)


def write_blocks(folder: Path, out_name: str, blocks: dict, meta: dict) -> Path:
    p = folder / out_name
    try:
        cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        cur = {}
    if not isinstance(cur, dict):
        cur = {}
    cur.update(blocks)
    cur["check_marks45"] = meta
    _atomic_write(p, cur)
    return p


def evaluate(folders: list[Path], refresh: bool = True, pytest_runner=_pytest,
             reference: str | None = None, anchors: list[Path] | None = None) -> tuple[dict, dict]:
    """``anchors``: validation-only folders (never launch models): read by the Everyday checks EV1/EV2/EV3/EV5
    only; never by a ceiling, a pair or a role of the launch board."""
    marks = _load_marks()
    runs = load_runs(folders, refresh)
    anchors = list(anchors or [])
    for f in anchors:
        runs += load_runs([f], refresh, anchor=True)
    clash = {r.label for r in runs if r.anchor} & {r.label for r in runs if not r.anchor}
    if clash:
        raise Integrity("an anchor folder holds a launch label: " + ", ".join(sorted(clash)))
    roles = dict(marks["segments"]["body"]["pair_rule"]["models"])
    if reference:
        roles["reference"] = reference          # identity only: which label is the reference model
    M = Models(runs, roles)
    seg = segment_marks(M, marks["segments"], folders, marks["pairs"]["body"])
    ev = everyday_marks(M, marks["everyday"], folders, seg, anchors)
    ab = actbeat_marks([r for r in runs if not r.anchor], marks["act_beat"], folders, pytest_runner)
    blocks = {"segments": seg, "everyday": ev, "act_beat": ab}
    meta = {"folders": [str(f) for f in folders],
            "marks_sha256": {k: marks[k]["sha"] for k in ("segments", "everyday", "act_beat", "evaluation", "pairs")}}
    return blocks, meta


def exit_code(blocks: dict) -> int:
    st = [r["status"] for b in blocks.values() for r in b.values()]
    return 1 if FAIL in st else 2 if NE in st else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--anchors", nargs="*", default=[], help="validation-only folders (anchors): read by the "
                    "Everyday checks EV1/EV2/EV3/EV5 only, never by a launch ceiling or pair")
    ap.add_argument("--out-name", default="marks45.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="do not re-make a stale report.json")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--reference", help="label pattern of the reference model (default: segment-marks-45.json "
                                        "pair_rule.models.reference); identity only")
    a = ap.parse_args(argv)
    folders = [Path(f) for f in a.folders]
    try:
        for f in folders + [Path(x) for x in a.anchors]:
            if not f.is_dir():
                raise Integrity(f"not a folder: {f}")
        if a.dry_run:
            marks = _load_marks()
            seg = marks["segments"]["body"]["segments"]
            print(f"folders: {len(folders)}; mark files indexed: " + ", ".join(
                f"{marks[k]['file']} ({marks[k]['sha'][:8]})" for k in marks))
            print(f"segment marks: {len(seg)} ({', '.join(seg)})")
            print("everyday marks: " + ", ".join(marks["everyday"]["body"]["marks"]))
            print("act-and-beat marks: " + ", ".join(marks["act_beat"]["body"]["marks"]))
            print(f"would write blocks segments, everyday, act_beat into <folder>/{a.out_name} (nothing written)")
            return 0
        blocks, meta = evaluate(folders, refresh=not a.no_refresh, reference=a.reference,
                                anchors=[Path(f) for f in a.anchors])
    except Integrity as e:
        print(f"integrity: {e}", file=sys.stderr)
        return 3
    for gname, g in blocks.items():
        for k, r in g.items():
            print(f"{r['status'].upper():14s} {r['id']:28s} {r['reason'][:240]}")
    if not a.no_write:
        for f in folders:
            write_blocks(f, a.out_name, blocks, meta)
    rc = exit_code(blocks)
    n = [r["status"] for b in blocks.values() for r in b.values()]
    print(f"marks: {n.count(PASS)} pass, {n.count(FAIL)} fail, {n.count(NE)} not evaluable")
    return rc


if __name__ == "__main__":
    sys.exit(main())
