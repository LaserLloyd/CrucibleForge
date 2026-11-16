#!/usr/bin/env python3
"""Archive one bench revision (questions + rubric only) into archive/<version>/.

    python3 scripts/archive_revision.py --rev v4.0.1 --version 4.0.1
    python3 scripts/archive_revision.py --rev 53f70c5 --version 3.0.0 \\
        [--src ~/Projects/crucibleforge] [--out archive/] [--notes TEXT]

Run it at every release (see docs/DEVELOPING.md). It reads the source repo
READ-ONLY through fixed-argv ``git`` calls (``rev-parse``, ``ls-tree``,
``show``); nothing is ever written to --src.

What is exported from the revision:
  cases/**            the public case tree (never private, never *.local.*)
  profiles/bench*.yaml  (never *.private.*)
  docs/{SCORING,CASES,JUDGE,CONTENT-RULES,PACKS}.md   those that exist at the rev

The scrub gate allows the fictional fixture home /home/alex/ (not the EL-17 gate).  # scrub-ok: fictional fixture home
Each exported file is gated before it is kept: scripts/scrub_check.py content
rules (plus the maintainer's scrub-rules.local.txt from --src when present;
hits on those are reported without the matched text), and for case files the
EL-17 stated-adult-ages rule. A failing file is NOT edited, it is dropped and
listed in MANIFEST.json ``excluded``. If the surviving cases tree is
byte-identical to an already archived version only MANIFEST.json is written,
with ``same_as``.

--complete (a RUNNABLE archive, e.g. 4.1.0 for `bench`): nothing is excluded. The
gate still runs, but a hit is recorded as ``gate_findings`` instead of dropping the
file. It refuses (exit 2, nothing written) when the gate has findings unless
``--frozen-for PROFILE`` names the profile the tree must stay runnable for; the
manifest then carries ``complete: true``, ``frozen_for`` and ``excluded == []``.
Never regenerate a version that already exists (the script refuses): a complete
tree must not replace one built by hand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

DOC_NAMES = ("SCORING.md", "CASES.md", "JUDGE.md", "CONTENT-RULES.md", "PACKS.md")
FIXTURE_HOME = "/home/alex/"  # scrub-ok: the fictional fixture home of the frozen task text
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def git(src: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(src), *args], capture_output=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"archive_revision: git {' '.join(args[:2])} failed: "
                         f"{r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def is_private_path(rel: str) -> bool:
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    return ("/private/" in "/" + low or ".local." in name or ".private." in name
            or name.startswith(".local") )


def wanted_paths(rel_paths: list[str]) -> list[str]:
    out = []
    for p in rel_paths:
        if is_private_path(p):
            continue
        parts = p.split("/")
        if parts[0] == "cases":
            out.append(p)
        elif len(parts) == 2 and parts[0] == "profiles" and re.fullmatch(r"bench[^/]*\.yaml", parts[1]):
            out.append(p)
        elif len(parts) == 2 and parts[0] == "docs" and parts[1] in DOC_NAMES:
            out.append(p)
    return sorted(out)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def case_dicts(data) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        data = data["cases"]
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict) and "id" in data:
        return [data]
    return []


def gate_rules(src: Path):
    """scrub_check content rules, plus the source repo's local identifier rules
    (compiled here so --src, not this checkout, decides; matched text is never
    printed for those)."""
    import scrub_check
    rules = list(scrub_check.CONTENT_RULES)
    rules += scrub_check.load_local_rules()
    local = src / "scripts" / "scrub-rules.local.txt"
    if local.is_file():
        for raw in local.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                try:
                    rules.append((re.compile(line, re.IGNORECASE), "a local private identifier"))
                except re.error:
                    raise SystemExit("archive_revision: bad regex in scrub-rules.local.txt")
    return scrub_check, rules


def check_file(rel: str, data: bytes, scrub_check, rules) -> tuple[str | None, int]:
    """(reason to exclude or None, case count)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, 0          # binary: nothing to scan
    for lineno, line in enumerate(text.splitlines(), 1):
        if scrub_check.OK_MARKER.search(line):
            continue
        # fictional fixture paths in the case texts are public (ship in the
        # v4.1.0 tag); any other home path still fails the scrub gate
        line = line.replace(FIXTURE_HOME, "/fixture/")
        for pattern, why in rules:
            if pattern.search(line):
                return f"scrub_check: line {lineno} looks like {why}", 0
    n = 0
    if rel.startswith("cases/") and rel.endswith(".json"):
        from crucibleforge.element_lint import lint_ages
        try:
            cases = case_dicts(json.loads(text))
        except ValueError:
            return None, 0
        n = len(cases)
        bad = [f"{c.get('id')}: {m}" for c in cases for m in lint_ages(c)]
        if bad:
            extra = f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""
            return f"EL-17 stated adult ages: {bad[0]}{extra}", 0
    return None, n


def _loads(data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def existing_cases_trees(out: Path) -> dict[str, dict[str, str]]:
    trees = {}
    for m in sorted(out.glob("*/MANIFEST.json")):
        d = json.loads(m.read_text(encoding="utf-8"))
        if d.get("same_as"):
            continue
        trees[d["version"]] = {k: v for k, v in d["files"].items() if k.startswith("cases/")}
    return trees


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rev", required=True, help="git rev (tag, sha) in --src")
    ap.add_argument("--version", required=True, help="X.Y.Z")
    ap.add_argument("--src", default="~/Projects/crucibleforge")
    ap.add_argument("--out", default=str(REPO / "archive"))
    ap.add_argument("--notes", default="")
    ap.add_argument("--complete", action="store_true",
                    help="keep every file (no exclusion); refuses on gate findings unless --frozen-for")
    ap.add_argument("--frozen-for", default=None, metavar="PROFILE",
                    help="with --complete: the profile this archive must stay runnable for "
                         "(allows gate findings, recorded in the manifest)")
    a = ap.parse_args()
    if a.frozen_for and not a.complete:
        raise SystemExit("archive_revision: --frozen-for needs --complete")
    if not VERSION_RE.match(a.version):
        raise SystemExit("archive_revision: --version must look like X.Y.Z")
    src = Path(a.src).expanduser().resolve()
    out = Path(a.out).expanduser().resolve()
    dest = out / a.version
    if dest.exists():
        raise SystemExit(f"archive_revision: {dest} already exists; remove it first")

    commit = git(src, "rev-parse", "--verify", a.rev + "^{commit}").decode().strip()
    date = git(src, "log", "-1", "--format=%cs", commit).decode().strip()
    tags = git(src, "tag", "--points-at", commit).decode().split()
    listing = git(src, "ls-tree", "-r", "-z", "--name-only", commit).decode().split("\0")
    paths = wanted_paths([p for p in listing if p])

    scrub_check, rules = gate_rules(src)
    kept: dict[str, bytes] = {}
    excluded: list[dict] = []
    findings: list[dict] = []
    case_count = 0
    for rel in paths:
        data = git(src, "show", f"{commit}:{rel}")
        why, n = check_file(rel, data, scrub_check, rules)
        if why and a.complete:
            findings.append({"path": rel, "reason": why})
            kept[rel] = data
            case_count += n or len(case_dicts(_loads(data)))
        elif why:
            excluded.append({"path": rel, "reason": why})
        else:
            kept[rel] = data
            case_count += n
    if findings and not a.frozen_for:
        raise SystemExit(f"archive_revision: --complete refused: {len(findings)} file(s) fail the gate "
                         f"(first: {findings[0]['path']}: {findings[0]['reason']}); "
                         "pass --frozen-for PROFILE to archive the tree whole")

    files = {rel: sha256(b) for rel, b in kept.items()}
    cases_tree = {k: v for k, v in files.items() if k.startswith("cases/")}
    same_as = None
    if cases_tree and not a.complete:      # a complete archive always holds its own files
        for ver, tree in existing_cases_trees(out).items():
            if tree == cases_tree:
                same_as = ver
                break

    manifest = {
        "version": a.version,
        "commit": commit,
        "date": date,
        "source_tag": tags[0] if tags else None,
        "files": {} if same_as else files,
        "case_count": case_count,
        "excluded": excluded,
        "notes": a.notes,
    }
    if a.complete:
        manifest["complete"] = True
        manifest["frozen_for"] = a.frozen_for
        manifest["gate_findings"] = findings
    if same_as:
        manifest["same_as"] = same_as

    dest.mkdir(parents=True)
    try:
        if not same_as:
            for rel, b in kept.items():
                p = dest / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b)
        (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    print(f"archived {a.version} @ {commit[:7]}: {len(kept)} files, {case_count} cases, "
          f"{len(excluded)} excluded" + (f", same_as {same_as}" if same_as else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
