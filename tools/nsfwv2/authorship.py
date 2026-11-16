"""Authorship ledger (REBALANCE-PLAN M7 / H6): fill and validate
``meta.authorship = {prose, elements, breaks, critics}`` per case from the build log.

    python -m tools.nsfwv2.authorship SETS_DIR BUILD_LOG.jsonl [--write]

Build-log rows carry ``pkg``, ``case``, ``step``, ``model``. Roles:
  prose     the WP5 / L3 ``cowrite`` and ``revise`` steps and every ``fit-*`` writer step
  elements  Claude, plus any model on a skeleton / directives / patch / edit-ops / codesign step
  breaks    Claude, plus any model on a ``fit-bad`` step (planted-break references)
  critics   ``critique`` steps
Model names are normalised (``claude-*`` -> claude, ``dsflash`` -> deepseek-flash,
``dspro`` -> deepseek-pro, ``a+b`` splits). Dry run by default; ``--write``
merges (union) into the set files given. Prints a table; exit 1 when a case
in the sets has no complete ledger.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ROLE_KEYS = ("prose", "elements", "breaks", "critics")
PROSE_PKGS = {"WP5", "L3"}
ELEMENT_STEPS = ("skeleton", "directives", "patch", "edit-ops", "codesign")
ALIASES = {"dsflash": "deepseek-flash", "dspro": "deepseek-pro", "ds": "deepseek"}


def norm_models(name: str) -> list[str]:
    out = []
    for part in str(name or "").lower().replace(",", "+").split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("claude"):
            part = "claude"
        part = ALIASES.get(part, part)
        out.append(part)
    return out


def classify(row: dict) -> set[str]:
    step, pkg = str(row.get("step") or ""), str(row.get("pkg") or "")
    roles = set()
    if step.startswith("fit-") or (pkg in PROSE_PKGS and step in ("cowrite", "revise")):
        roles.add("prose")
    if step == "fit-bad":
        roles.add("breaks")
    if step.startswith(ELEMENT_STEPS):
        roles.add("elements")
    if step == "critique":
        roles.add("critics")
    return roles


def build(log_path) -> dict:
    led = {}
    for line in open(log_path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        case = r.get("case")
        if not case:
            continue
        roles = classify(r)
        if not roles:
            continue
        d = led.setdefault(case, {k: set() for k in ROLE_KEYS})
        for m in norm_models(r.get("model")):
            for role in roles:
                d[role].add(m)
    for d in led.values():
        d["elements"].add("claude")
        d["breaks"].add("claude")
    return {c: {k: sorted(v) for k, v in d.items()} for c, d in led.items()}


def heldout_hits(merged_by_case: dict, extra=(), cases=None) -> list[tuple[str, str, str]]:
    """[(case, role, model)] where a held-out subject appears in prose/critics
    (the roles a held-out model must never take). ``merged_by_case``: {case: ledger}."""
    from crucibleforge import report
    out = []
    for cid, led in merged_by_case.items():
        extra_c = list(extra) + list((led or {}).get("heldout") or [])
        for role in ("prose", "critics"):
            for m in (led or {}).get(role) or []:
                if report.is_heldout(m, cases, extra_c):
                    out.append((cid, role, m))
    return out


def validate(auth) -> list[str]:
    """Problems with a stored ``meta.authorship`` (empty list = complete)."""
    if not isinstance(auth, dict):
        return ["missing"]
    bad = []
    h = auth.get("heldout")
    if h is not None and (not isinstance(h, list) or not all(isinstance(x, str) for x in h)):
        bad.append("heldout: not a list of names")
    for k in ROLE_KEYS:
        v = auth.get(k)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            bad.append(f"{k}: not a list of names")
        elif k != "critics" and not v:
            bad.append(f"{k}: empty")
    return bad


def set_files(sets_dir):
    return sorted(glob.glob(os.path.join(sets_dir, "*.json")))


def plan(sets_dir, log_path):
    """-> {file: {case: (current, merged)}}"""
    led = build(log_path)
    out = {}
    for f in set_files(sets_dir):
        doc = json.load(open(f))
        for c in doc.get("cases", []) if isinstance(doc, dict) else []:
            cur = (c.get("meta") or {}).get("authorship")
            new = led.get(c.get("id"), {k: [] for k in ROLE_KEYS})
            merged = {k: sorted(set(new.get(k, [])) | set((cur or {}).get(k, []) if isinstance(cur, dict) else []))
                      for k in ROLE_KEYS}
            if isinstance(cur, dict) and cur.get("heldout"):
                merged["heldout"] = sorted(set(cur["heldout"]))
            out.setdefault(f, {})[c.get("id")] = (cur, merged)
    return out


def apply(plan_, write=False):
    n = 0
    for f, cases in plan_.items():
        doc = json.load(open(f))
        changed = False
        for c in doc["cases"]:
            cur, merged = cases[c["id"]]
            if cur != merged:
                n += 1
                if write:
                    c.setdefault("meta", {})["authorship"] = merged
                    changed = True
        if write and changed:
            with open(f, "w") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sets_dir")
    ap.add_argument("build_log")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--heldout", nargs="*", default=[],
                    help="extra held-out model id prefixes (default: hermes-4.3-36b and each case's meta.authorship.heldout)")
    a = ap.parse_args(argv)
    pl = plan(a.sets_dir, a.build_log)
    hits = heldout_hits({cid: m for cases in pl.values() for cid, (_c, m) in cases.items()}, a.heldout)
    if hits and a.write:
        print("REFUSED: a held-out subject would be written into prose/critics; nothing written:")
        for cid, role, m in hits:
            print(f"  {cid}: {role} <- {m}")
        return 2
    print("| case | prose | elements | breaks | critics | stored | problems |")
    print("|---|---|---|---|---|---|---|")
    incomplete = 0
    for f, cases in pl.items():
        for cid, (cur, merged) in cases.items():
            prob = validate(merged)
            incomplete += bool(prob)
            print(f"| {cid} | {','.join(merged['prose']) or '-'} | {','.join(merged['elements'])} | "
                  f"{','.join(merged['breaks'])} | {','.join(merged['critics']) or '-'} | "
                  f"{'same' if cur == merged else ('none' if cur is None else 'differs')} | {'; '.join(prob)} |")
    if hits:
        print(f"\nWARNING: {len(hits)} held-out subject(s) in prose/critics (--write would refuse).")
    n = apply(pl, a.write)
    print(f"\n{n} case(s) {'written' if a.write else 'would change (dry run; --write applies)'}; "
          f"{incomplete} incomplete.")
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
