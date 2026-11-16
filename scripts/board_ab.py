#!/usr/bin/env python3
"""Board A/B: the same results directory scored by two source trees.

Guards the rule that a change meant for one profile does not move another
profile's board: every model's Total, every subscore, every score label and
the WHOLE shipped board row (report.html_rows, recursively: components, s/m,
maxed, ladder, wc, cost fields...) and the whole report.json each tree's
``report.generate`` writes (model_stats and every other section; only the
VOLATILE write time is skipped) must be identical between the two trees
(exit 1 lists what changed).

    uv run python scripts/board_ab.py --results <results dir> --base <git rev>
        [--head <git rev>] [--profile bench] [--out ab.json] [--workdir <dir>]

``--base``/``--head`` are git revisions of this repository, exported with
``git archive`` into ``--workdir`` (default: a temporary directory); no
``--head`` scores with this checkout. Each tree runs in its own Python process
(``report.board_stats`` with the profile made the default one), with
``CRUCIBLEFORGE_NO_PRIVATE=1`` and no models.yaml, so only what the results
directory holds is read. Point it at a COPY of a results directory, never at
the live one of a running bench. One tree can also be dumped alone:
``--dump <tree>`` prints that tree's board as JSON."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: absolute tolerance on a points value (the trees must agree exactly; this
#: only absorbs float noise from a different summation order)
TOL = 1e-9


#: the reserved key of a dump that holds the whole report.json
REPORT_JSON = "__report_json__"
#: report.json fields that differ between any two runs of the same tree (the
#: write time): the ONLY fields the comparison skips, by exact path
VOLATILE = frozenset({"generated"})


def dump(results: str, profile: str) -> dict:
    """{label: {total, rank, labels, parts, row}} of the board this process's
    ``crucibleforge`` package draws from ``results`` under ``profile``; ``row``
    is the WHOLE shipped board row (report.html_rows, as the site's JSON
    carries it). Under ``REPORT_JSON``: the whole report.json the same tree's
    ``report.generate`` writes (models / model_stats, separation, every
    section), captured in memory — nothing is written to ``results``."""
    from crucibleforge import config, profiles, report
    config.set_results_dir(Path(results))
    profiles.DEFAULT_PROFILE = profile
    target = Path(results).resolve()
    real_bs, real_wt = report.board_stats, Path.write_text
    board, written = [], {}

    def board_stats(*a, **k):
        out = real_bs(*a, **k)
        board.append(out)
        return out

    def write_text(self, data, *a, **k):
        if Path(self).resolve().parent == target:
            written[Path(self).name] = data         # captured, never written
            return len(data)
        return real_wt(self, data, *a, **k)

    report.board_stats, Path.write_text = board_stats, write_text
    try:
        report.generate(None, write=True)
    finally:
        report.board_stats, Path.write_text = real_bs, real_wt
    labels, stats, _ = board[0]
    rows = {r["label"]: r for r in report.html_rows(labels, stats, None)}
    out = {}
    for l in labels:
        s = stats[l]
        card = s.get("scorecard") or {}
        out[l] = {"total": card.get("total"), "rank": (s.get("headline") or {}).get("rank"),
                  "labels": card.get("labels") or {}, "parts": card.get("parts") or {},
                  "row": json.loads(json.dumps(rows.get(l) or {}, default=str))}
    out[REPORT_JSON] = json.loads(written["report.json"])
    return out


def _tree_of(rev: str | None, workdir: Path, name: str) -> Path:
    if rev is None:
        return ROOT
    dest = workdir / name
    dest.mkdir(parents=True, exist_ok=True)
    arch = subprocess.run(["git", "-C", str(ROOT), "archive", rev],
                          check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arch, check=True)
    return dest


def _run_tree(tree: Path, results: str, profile: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("CRUCIBLEFORGE_CONFIG", "PYTHONPATH")}
    env["CRUCIBLEFORGE_NO_PRIVATE"] = "1"
    env["PYTHONPATH"] = str(tree)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
            "sys.path.insert(0, sys.argv[2]); import board_ab; "
            "print(json.dumps(board_ab.dump(sys.argv[3], sys.argv[4]), sort_keys=True))")
    p = subprocess.run([sys.executable, "-c", code, str(tree), str(Path(__file__).parent),
                        results, profile], cwd=str(tree), env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"{tree}: board failed\n{p.stderr[-4000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def _leaves(v, path: str, out: dict) -> dict:
    if isinstance(v, dict):
        if not v:
            out[path] = {}
        for k, x in v.items():
            _leaves(x, f"{path}/{k}" if path else str(k), out)
    elif isinstance(v, list):
        if not v:
            out[path] = []
        for i, x in enumerate(v):
            _leaves(x, f"{path}[{i}]", out)
    else:
        out[path] = v
    return out


def _diff_leaves(fa: dict, fb: dict, empty_only: dict) -> dict:
    diff = {}
    for k in sorted(set(fa) | set(fb)):
        if k in fa and k in fb:
            if not _same(fa[k], fb[k]):
                diff[k] = [fa[k], fb[k]]
        elif _empty(fa.get(k) if k in fa else fb.get(k)):
            key = k.split("[", 1)[0]
            empty_only[key] = empty_only.get(key, 0) + 1
        else:
            diff[k] = [fa.get(k), fb.get(k)]
    return diff


def compare_report_json(a: dict | None, b: dict | None) -> dict:
    """The whole report.json of the two trees, leaf by leaf (the rules of
    :func:`compare`), except the VOLATILE paths. ``by_section``: changed
    leaves per top-level section / model ("models/<label>")."""
    if a is None or b is None:
        return {"changed": 0 if a is b else 1, "detail": {"missing": [a is None, b is None]},
                "by_section": {}, "empty_only_in_one": {}}
    fa = {k: v for k, v in _leaves(a, "", {}).items() if k not in VOLATILE}
    fb = {k: v for k, v in _leaves(b, "", {}).items() if k not in VOLATILE}
    empty_only: dict = {}
    diff = _diff_leaves(fa, fb, empty_only)
    sec: dict = {}
    for k in diff:
        parts = k.split("/")
        key = "/".join(parts[:2]) if parts[0] == "models" else parts[0].split("[", 1)[0]
        sec[key] = sec.get(key, 0) + 1
    return {"changed": len(diff), "detail": dict(list(diff.items())[:200]),
            "by_section": sec, "empty_only_in_one": empty_only}


def _flat(m: dict) -> dict:
    """Every leaf of a model's dump, recursively ({"row/components/Chat": 7.1,
    "row/wc/nsfw/uncensored/n": 3, "label/work": "9.5", "parts/nsfw/uncensored":
    2.8, ...}): the totals, the score labels and every field of the shipped
    row, nested dicts and lists included (Fable checkpoints 10-11)."""
    out: dict = {"total": m.get("total")}
    _leaves(m.get("labels") or {}, "label", out)
    _leaves(m.get("parts") or {}, "parts", out)
    _leaves(m.get("row") or {}, "row", out)
    return out


def _empty(v) -> bool:
    """A field only one tree ships is no change when it carries nothing."""
    return v is None or v == "" or v == [] or v == {} or v is False or (
        isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)


def _same(a, b) -> bool:
    """Equal leaves. Numbers agree within TOL (1e-9: the shipped JSON carries
    full precision, so this is at least as strict as any shipped rounding and
    only absorbs summation-order noise); everything else compares exactly."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(a) and num(b):
        return abs(float(a) - float(b)) <= TOL
    return a == b and type(a) is type(b)


def compare(base: dict, head: dict) -> dict:
    changed, empty_only = {}, {}
    for l in sorted((set(base) | set(head)) - {REPORT_JSON}):
        if l not in base or l not in head:
            changed[l] = {"only_in": "base" if l in base else "head"}
            continue
        diff = _diff_leaves(_flat(base[l]), _flat(head[l]), empty_only)
        if base[l].get("rank") != head[l].get("rank"):
            diff["rank"] = [base[l].get("rank"), head[l].get("rank")]
        if diff:
            changed[l] = diff
    rj = compare_report_json(base.get(REPORT_JSON), head.get(REPORT_JSON)) \
        if (REPORT_JSON in base or REPORT_JSON in head) else None
    return {"models": len((set(base) | set(head)) - {REPORT_JSON}), "changed": len(changed),
            "detail": changed,
            # empty fields one tree ships and the other does not (info only)
            "empty_only_in_one": empty_only,
            **({"report_json": rj} if rj is not None else {})}


#: the default allow-list of ``--report-allow`` (G-41Freeze, Fable checkpoint 15)
ALLOW_FILE = Path(__file__).with_name("board_ab_allow.yaml")


def load_allow(path: str | Path = ALLOW_FILE) -> list[dict]:
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = []
    for e in data.get("allow") or []:
        if e.get("kind") not in ("new_key", "null_to_neutral") or not e.get("reason"):
            raise SystemExit(f"{path}: bad allow entry {e!r} (kind new_key|null_to_neutral + reason)")
        out.append(e)
    return out


def _segs(path: str, model_level: bool) -> list[str]:
    """A report.json leaf path as matchable segments: models/<label> ->
    models/<model>, a list index -> <i> (so a glob never reads it as a class)."""
    segs = re.sub(r"\[(?:\d+|\*)\]", "/<i>", path).split("/")
    if model_level and len(segs) > 1 and segs[0] == "models":
        segs[1] = "<model>"
    return segs


def _matches(entry: str, path: str, prefix: bool) -> bool:
    e, p = _segs(entry, False), _segs(path, True)
    if len(p) < len(e) or (not prefix and len(p) != len(e)):
        return False
    return all(fnmatch.fnmatchcase(x, y) for x, y in zip(p, e))


def _neutral(v) -> bool:
    return v == "standard" or _empty(v)


def classify_report_json(a: dict, b: dict, allow: list[dict]) -> dict:
    """G-41Freeze (b): every report.json difference must be ADDITIVE and listed.
    A leaf only ``b`` has is a new key (allowed under a ``new_key`` entry that
    is a prefix of it); a null that became a neutral default is allowed under
    an exact ``null_to_neutral`` entry; anything else (a value change, a
    removed key, an unlisted addition) is a violation. VOLATILE is skipped."""
    fa = {k: v for k, v in _leaves(a, "", {}).items() if k not in VOLATILE}
    fb = {k: v for k, v in _leaves(b, "", {}).items() if k not in VOLATILE}
    allowed: dict = {}
    viol: dict = {}
    kinds: dict = {}
    for k in sorted(set(fa) | set(fb)):
        if k in fa and k in fb:
            if _same(fa[k], fb[k]):
                continue
            kind = "null_to_neutral" if fa[k] is None and _neutral(fb[k]) else "value_change"
        else:
            kind = "new_key" if k in fb else "removed"
        hit = next((e for e in allow if e["kind"] == kind
                    and _matches(e["path"], k, prefix=kind == "new_key")), None)
        if hit is not None:
            allowed[hit["path"]] = allowed.get(hit["path"], 0) + 1
        else:
            kinds[kind] = kinds.get(kind, 0) + 1
            if len(viol) < 200:
                viol[k] = [fa.get(k), fb.get(k), kind]
    return {"n_allowed": sum(allowed.values()), "allowed": allowed,
            "n_violations": sum(kinds.values()), "violation_kinds": kinds,
            "violations": viol}


def _sha(rev: str | None) -> str:
    """The commit sha a tree was scored from; the checkout is "<sha>-dirty"
    when it has uncommitted tracked changes."""
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", rev or "HEAD"],
                       capture_output=True, text=True)
    sha = r.stdout.strip() or str(rev)
    if rev is None:
        st = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "-uno"],
                            capture_output=True, text=True).stdout.strip()
        sha += "-dirty" if st else ""
    return sha


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--profile", default="bench")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--dump", help="print one tree's board as JSON and exit")
    ap.add_argument("--out")
    ap.add_argument("--workdir")
    ap.add_argument("--report-allow", nargs="?", const=str(ALLOW_FILE), default=None,
                    metavar="YAML",
                    help="G-41Freeze: rows identical AND every report.json difference "
                         "additive and on the allow-list (default scripts/board_ab_allow.yaml)")
    a = ap.parse_args()
    results = str(Path(a.results).resolve())
    if a.dump:
        print(json.dumps(_run_tree(Path(a.dump).resolve(), results, a.profile),
                         sort_keys=True, indent=1))
        return 0
    if not a.base:
        ap.error("--base is required (or --dump)")
    with tempfile.TemporaryDirectory(dir=a.workdir) as td:
        base = _run_tree(_tree_of(a.base, Path(td), "base"), results, a.profile)
        head = _run_tree(_tree_of(a.head, Path(td), "head"), results, a.profile)
    res = compare(base, head)
    res.update(base_rev=_sha(a.base), head_rev=_sha(a.head), profile=a.profile)
    if a.report_allow:
        res["report_allow"] = classify_report_json(base.get(REPORT_JSON) or {},
                                                   head.get(REPORT_JSON) or {},
                                                   load_allow(a.report_allow))
    if a.out:
        Path(a.out).write_text(json.dumps({"result": res, "base": base, "head": head},
                                          sort_keys=True, indent=1) + "\n", encoding="utf-8")
    rj = res.get("report_json") or {}
    print(f"base {res['base_rev']} head {res['head_rev']}")
    print(f"profile {a.profile}: {res['changed']} of {res['models']} models changed "
          f"(whole shipped row compared)")
    for l, d in res["detail"].items():
        print(f"  {l}: {json.dumps(d)[:2000]}")
    print(f"report.json: {rj.get('changed', 0)} leaves changed "
          f"(volatile skipped: {sorted(VOLATILE)}); by section: {json.dumps(rj.get('by_section') or {})}")
    for k, v in list((rj.get("detail") or {}).items())[:40]:
        print(f"  {k}: {json.dumps(v)[:300]}")
    ra = res.get("report_allow")
    if ra is not None:
        print(f"report-allow ({a.report_allow}): {ra['n_allowed']} additive leaves allowed "
              f"over {len(ra['allowed'])} entries, {ra['n_violations']} violations "
              f"{json.dumps(ra['violation_kinds'])}")
        for p_, n in sorted(ra["allowed"].items()):
            print(f"  allowed {n:6d}  {p_}")
        for k, v in list(ra["violations"].items())[:60]:
            print(f"  VIOLATION {v[2]}: {k}: {json.dumps(v[:2])[:300]}")
        return 1 if (res["changed"] or ra["n_violations"]) else 0
    return 1 if (res["changed"] or rj.get("changed")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
