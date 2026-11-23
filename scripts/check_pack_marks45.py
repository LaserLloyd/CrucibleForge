#!/usr/bin/env python3
"""Evaluate the Chat-pack marks (P1-P5, calibration/pack-marks-chat-45-addendum-20261120.json) from stored rows and
the stored second-reader records. No model call.

usage: check_pack_marks45.py --results <dir> --folders <run folder>... [--anchors <run folder>...] [--no-write]
                             [--skip-p4]

Reads every folder's transcripts (one model each; folders of one label are pooled) and its ``readers45/`` store,
scores the pack scenes as the board does (packs45.scene_scores: mean of judge and pinned second reader), then:

  P1  largest judge term (rplong_mean.judge_term) over the models with >= 8 read pack scene units <= 0.05
  P2  per segment, the sonnet-class run above the haiku-class run on the judged level by MORE than the combined margin
      (inside it, a tie or a reversal = fail; only the margin combination is check_marks45.pair_part's). A role is a run
      label containing ``sonnet`` / ``haiku`` (case-insensitive; calibration/pack-marks-chat-45-addendum-20261120b.json).
      One model's margin = headline.margin over a seeded bootstrap of its judged scenes (headline.draws with one
      stratum per (segment, label), so no stream is shared with a standard draw); the pair's = sqrt(ma^2 + mb^2).
  P3  per segment, the best judged share on file < 0.97 (and the best generated-level share, once reached)
  P4  the generated levels' key audit (a pytest node over gen.verify_case)
  P5  the unlock is the profile's ladder.unlock (0.9)

A run of a role that does not exist reads "order not tested": P2 is not evaluable and the pack prints no number.
Writes ``<results>/marks45.json`` key ``chat_packs`` (atomic read-modify-write). Exit 0 all pass, 1 a mark failed,
2 something not evaluable."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crucibleforge import agree as AG            # noqa: E402
from crucibleforge import packs45 as PK          # noqa: E402
from crucibleforge import rplong_mean as RM      # noqa: E402

MARKS_FILE = "pack-marks-chat-45-addendum-20261120.json"
IDENTITY_FILE = "pack-marks-chat-45-addendum-20261120b.json"
PASS, FAIL, NE = "pass", "fail", "not_evaluable"
#: the judged level is worth one point (``ladder`` pack points), so a share is also its points
PACK_POINTS = 1.0
#: role -> label substring (the addendum 20261120b; the same substrings judge-marks-45.json d_known_order uses)
ROLES = {"sonnet-class": "sonnet", "haiku-class": "haiku"}
P1_MAX_TERM = 0.05
P4_NODE = "tests/test_packs45_chat.py::test_generated_levels_materialise_and_key_audit"


def _res(mid: str, status: str, measured, threshold: str, reason: str = "") -> dict:
    return {"id": f"pack:{mid}", "status": status, "measured": measured, "threshold": threshold, "reason": reason}


# ------------------------------------------------------------------ P2 pieces
def role_labels(labels: list[str], sub: str) -> list[str]:
    return sorted({l for l in labels if sub in l.lower()})


def stream_seed(segment: str, label: str) -> int:
    """One seeded stream per (segment, label): never shared with a standard draw or another label."""
    return int(hashlib.sha256(f"packP2|{segment}|{label}".encode()).hexdigest()[:16], 16)


def segment_margin(segment: str, label: str, means: dict[str, float]) -> float:
    """One model's 90% margin (points) on a pack's judged level: the board's own ``headline.margin`` (half the 90%
    interval of the resamples, 0.1 grid, floor 0.1) over ``headline.RESAMPLES`` bootstrap resamples of the judged
    scenes (each drawn with replacement; ``headline.draws`` with a single stratum)."""
    from crucibleforge import headline as H
    cases = sorted(means)
    draws = H.draws({(1, f"packP2|{segment}|{label}"): cases}, H.RESAMPLES, stream_seed(segment, label), per_stratum=True)
    samples = []
    for d in draws:
        n = sum(len(v) for v in d.values())
        samples.append(PACK_POINTS * sum(means[c] * len(v) for c, v in d.items()) / n)
    return float(H.margin(samples))


def combined_margin(ma: float, mb: float) -> float:
    """The combined margin of two models (segment-marks-45.json "margin"; check_marks45.pair_part): sqrt(ma^2 + mb^2)."""
    return math.sqrt(ma * ma + mb * mb)


def p2_for_segment(segment: str, labels: list[str], means_by_label: dict[str, dict[str, float]]) -> dict:
    """P2 for one segment. ``labels`` = every run label on the board; ``means_by_label`` = {label: {case: mean}} for
    the labels whose judged level is fully read."""
    thr = "sonnet-class above haiku-class on the judged level by more than the combined margin of the two"
    picked, none, other = {}, [], []
    for role, sub in ROLES.items():
        ls = role_labels(labels, sub)
        if not ls:
            none.append(role)
        elif len(ls) > 1:
            other.append(f"{role} is ambiguous: {', '.join(ls)}")
        elif ls[0] not in means_by_label:
            other.append(f"{role} run {ls[0]} has no fully read judged level for this segment")
        else:
            picked[role] = ls[0]
    if none or other:
        parts = ([f"no run of {', '.join(none)}"] if none else []) + other
        return _res("P2", NE, None, thr, "P2 known-order pair: order not tested (" + "; ".join(parts) + ")")
    la, lb = picked["sonnet-class"], picked["haiku-class"]
    ma, mb = means_by_label[la], means_by_label[lb]
    va, vb = PACK_POINTS * sum(ma.values()) / len(ma), PACK_POINTS * sum(mb.values()) / len(mb)
    comb = combined_margin(segment_margin(segment, la, ma), segment_margin(segment, lb, mb))
    diff = va - vb
    meas = {"a": la, "b": lb, "a_points": round(va, 4), "b_points": round(vb, 4), "diff": round(diff, 4),
            "combined_margin": round(comb, 4)}
    if diff > comb + 1e-12:
        return _res("P2", PASS, {**meas, "state": "separated"}, thr)
    if -diff > comb + 1e-12:
        return _res("P2", FAIL, meas, thr, f"P2 known-order pair: order reversed ({la} {va:.3f} vs {lb} {vb:.3f}, "
                                           f"diff {diff:.3f}, combined margin {comb:.3f})")
    return _res("P2", FAIL, meas, thr, f"P2 known-order pair: not separated (diff {diff:.3f}, combined margin "
                                       f"{comb:.3f})")


# ------------------------------------------------------------------ P1, P3, P4, P5
def p1_mark(per_model: dict[str, dict]) -> dict:
    """``per_model`` = {label: packs45.p1_reader_agreement(...)}; the mark takes the largest term over the models
    measured (>= 8 read units each); a model not measured never hides one that is."""
    thr = f"largest judge term over the models measured <= {P1_MAX_TERM} (min 8 read scene units per model)"
    got = {l: p for l, p in per_model.items() if p.get("state") == "measured" and p.get("term") is not None}
    if not got:
        return _res("P1", NE, {"models": {l: p.get("n") for l, p in per_model.items()}}, thr,
                    "P1 reader agreement not measured (no model has >= 8 pack scene units read by both readers)")
    worst = max(got, key=lambda l: got[l]["term"])
    t = got[worst]["term"]
    meas = {"max_term": round(t, 4), "model": worst, "terms": {l: round(p["term"], 4) for l, p in got.items()}}
    if t > P1_MAX_TERM + 1e-12:
        return _res("P1", FAIL, meas, thr, f"P1 failed: judge term {t:.3f} on {worst} is over {P1_MAX_TERM}")
    return _res("P1", PASS, meas, thr)


def p3_for_segment(segment: str, shares: dict[str, float], code_shares: dict[str, float] | None = None) -> dict:
    thr = f"best judged share on file < {PK.P3_MAX} (and the best generated level, once reached)"
    if not shares:
        return _res("P3", NE, None, thr, "P3 not measured: no model has a fully read judged level for this segment")
    best_l = max(shares, key=lambda l: shares[l])
    best = shares[best_l]
    codes = {l: c for l, c in (code_shares or {}).items() if c is not None and shares.get(l, 0) >= PK.UNLOCK}
    meas = {"best_judged": round(best, 4), "model": best_l,
            "best_code": round(max(codes.values()), 4) if codes else None}
    if best >= PK.P3_MAX - 1e-12:
        return _res("P3", FAIL, meas, thr, f"P3 failed: {best_l} reaches {best:.3f} on the judged level: MAXED")
    if codes and max(codes.values()) >= PK.P3_MAX - 1e-12:
        return _res("P3", FAIL, meas, thr, f"P3 failed: a generated level reaches {max(codes.values()):.3f}: MAXED")
    return _res("P3", PASS, meas, thr)


def _pytest_node(node: str) -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="packmarks45-pt-", dir=os.environ.get("MARKS45_TMP", "/var/tmp"))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp",
                            str(Path(tmp) / "bt"), str(ROOT / node)], cwd=ROOT, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return r.returncode == 0, (r.stdout.strip().splitlines() or [""])[-1]


def p4_mark(pytest_runner=_pytest_node) -> dict:
    thr = "gen.verify_case returns no problem for every generated level (reference 1.0, every mutant < 1.0)"
    ok, tail = pytest_runner(P4_NODE)
    return _res("P4", PASS if ok else FAIL, {"test": P4_NODE, "result": tail}, thr, "" if ok else f"P4 failed: {tail}")


def p5_mark() -> dict:
    import yaml
    thr = "the unlock is the profile's ladder.unlock (0.9), as written"
    try:
        v = float(((yaml.safe_load((ROOT / "profiles/bench45.yaml").read_text(encoding="utf-8")) or {})
                   .get("ladder") or {}).get("unlock"))
    except (OSError, ValueError, TypeError):
        return _res("P5", NE, None, thr, "P5: the profile's ladder.unlock is unreadable")
    ok = abs(v - PK.UNLOCK) < 1e-12
    return _res("P5", PASS if ok else FAIL, {"profile_unlock": v, "packs45.UNLOCK": PK.UNLOCK}, thr,
                "" if ok else f"P5 failed: profile unlock {v} is not {PK.UNLOCK}")


#: every mark id of the marks file -> its evaluator (a test pins that none is missing)
EVALUATORS = {"P1_reader_agreement": p1_mark, "P2_known_order": p2_for_segment, "P3_not_maxed": p3_for_segment,
              "P4_key_audit_code_levels": p4_mark, "P5_unlock": p5_mark}


# ------------------------------------------------------------------ the block
def evaluate_block(scores_by_label: dict[str, dict], labels: list[str], code_shares: dict | None = None,
                   p4: dict | None = None, p5: dict | None = None) -> dict:
    """The ``chat_packs`` block from {label: packs45.scene_scores(...)} (pure; the marks of every segment)."""
    code_shares = code_shares or {}
    p1 = p1_mark({l: PK.p1_reader_agreement(sc) for l, sc in scores_by_label.items()})
    p4, p5 = p4 or p4_mark(), p5 or p5_mark()
    segs = {}
    for seg in PK.SEGMENTS:
        means = {l: m for l, sc in scores_by_label.items() for m in [PK.scene_means(sc, seg)[0]] if m}
        shares = {l: sum(m.values()) / len(m) for l, m in means.items()}
        codes = {l: (code_shares.get(l) or {}).get(seg) for l in means}
        segs[seg] = {"P2_known_order": p2_for_segment(seg, labels, means),
                     "P3_not_maxed": p3_for_segment(seg, shares, codes)}
    return {"P1_reader_agreement": p1, "segments": segs, "P4_key_audit_code_levels": p4,
            "P5_unlock": p5}


def _code_share(folder: Path, label: str, segment: str) -> float | None:
    try:
        m = (json.loads((folder / "report.json").read_text(encoding="utf-8")).get("models") or {}).get(label) or {}
        sets = {x["n"]: x for x in (((m.get("scorecard") or {}).get("ladder") or {}).get("chat") or {})
                .get(PK.PART_OF[segment], {}).get("sets") or []}
        return (sets.get(2) or {}).get("share")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def evaluate(folders: list[Path], anchors: list[Path] | None = None, pytest_runner=_pytest_node,
             score_fn=None, skip_p4: bool = False) -> dict:
    from crucibleforge import marksindex45
    sha = marksindex45.require(ROOT / "calibration" / MARKS_FILE)
    isha = marksindex45.require(ROOT / "calibration" / IDENTITY_FILE)
    rows: dict[str, list[dict]] = {}
    recs: dict[str, list[dict]] = {}
    code: dict[str, dict] = {}
    for f in [*folders, *(anchors or [])]:
        label, rs = AG.read_folder(Path(f))
        rows.setdefault(label, []).extend(rs)
        recs.setdefault(label, []).extend(RM.load_store(Path(f), label).values())
        for seg in PK.SEGMENTS:
            c = _code_share(Path(f), label, seg)
            if c is not None:
                code.setdefault(label, {})[seg] = c
    cases = PK.case_map()
    score_fn = score_fn or (lambda l, r, c: PK.scene_scores(r, c, label=l, cases=cases))
    scores = {l: score_fn(l, rows[l], recs.get(l, [])) for l in rows}
    p4 = _res("P4", NE, None, "skipped", "P4 not run (--skip-p4)") if skip_p4 else p4_mark(pytest_runner)
    blk = evaluate_block(scores, sorted(rows), code, p4=p4, p5=p5_mark())
    blk.update({"marks_file": MARKS_FILE, "marks_sha256": sha, "identity_file": IDENTITY_FILE,
                "identity_sha256": isha, "labels": sorted(rows),
                "folders": [str(f) for f in [*folders, *(anchors or [])]]})
    return blk


def _statuses(blk: dict) -> list[str]:
    out = [blk[k]["status"] for k in ("P1_reader_agreement", "P4_key_audit_code_levels", "P5_unlock")]
    for s in blk["segments"].values():
        out += [m["status"] for m in s.values()]
    return out


def write_block(results: Path, blk: dict) -> Path:
    p = Path(results) / "marks45.json"
    try:
        cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        cur = {}
    cur = cur if isinstance(cur, dict) else {}
    cur["chat_packs"] = blk
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(cur, indent=1, default=list), encoding="utf-8")
    os.replace(tmp, p)
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--anchors", nargs="*", default=[])
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--skip-p4", action="store_true")
    a = ap.parse_args(argv)
    try:
        blk = evaluate([Path(f) for f in a.folders], [Path(f) for f in a.anchors], skip_p4=a.skip_p4)
    except Exception as e:  # noqa: BLE001 - a mark that cannot be read is never a pass
        print(f"check_pack_marks45: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    print(f"{blk['P1_reader_agreement']['status'].upper():14s} P1 {blk['P1_reader_agreement']['reason'][:200]}")
    for seg, ms in blk["segments"].items():
        for m in ms.values():
            print(f"{m['status'].upper():14s} {seg:10s} {m['id']} {m['reason'][:200]}")
    for k in ("P4_key_audit_code_levels", "P5_unlock"):
        print(f"{blk[k]['status'].upper():14s} {blk[k]['id']} {blk[k]['reason'][:200]}")
    if not a.no_write:
        print("result:", write_block(Path(a.results), blk))
    st = _statuses(blk)
    return 1 if FAIL in st else 2 if NE in st else 0


if __name__ == "__main__":
    sys.exit(main())
