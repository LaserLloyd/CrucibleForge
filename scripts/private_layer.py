#!/usr/bin/env python3
"""Back up, restore and inspect the LOCAL-ONLY layer of this checkout.

The local layer is everything git deliberately ignores that you cannot simply
regenerate: private case files, private profiles and overlays, the private
scrub rules, operator-local settings. Git never carries it, so `git pull`,
`git merge`, `git checkout` and `git stash` leave it alone — but
`git clean -x` / `-X`, `git stash --all`, deleting the checkout or re-cloning
lose it for good. This script is the safety net (docs/CUSTOM-TESTS.md,
"Keeping a custom test local-only").

    python3 scripts/private_layer.py status            # files + sha256
    python3 scripts/private_layer.py backup            # timestamped tarball
    python3 scripts/private_layer.py backup --if-changed
    python3 scripts/private_layer.py list              # backups, newest first
    python3 scripts/private_layer.py restore latest    # dry run: what would change
    python3 scripts/private_layer.py restore latest --go
    python3 scripts/private_layer.py check             # warn if files went missing

Backups go to $CRUCIBLEFORGE_PRIVATE_BACKUP_DIR, default
~/Documents/Backups/crucibleforge-private (directory 0700, archives 0600). The
newest --keep (default 20) archives are kept. `restore --go` first backs up the
current layer (label "pre-restore"), then writes the archive's files back,
overwriting the ones that differ. Results (results/) are NOT part of the layer:
they are large and have their own backups.

Standard library only, so it runs from a git hook on a bare system Python.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: the local layer, as globs relative to the repo root (keep in step with
#: .gitignore and with scrub_check.FORBIDDEN_IF_COMMITTED)
LAYER_GLOBS = (
    "cases/private/**/*",
    "profiles/private/**/*",
    "profiles/*.private.yaml",
    "scripts/scrub-rules.local.txt",
    "crucibleforge/canary_explicit.local.txt",
    "crucibleforge.local.env",
    "models.yaml",
)
PREFIX = "private-layer-"
MANIFEST = "MANIFEST.sha256"


def backup_dir() -> Path:
    env = os.environ.get("CRUCIBLEFORGE_PRIVATE_BACKUP_DIR")
    return Path(env).expanduser() if env else Path.home() / "Documents" / "Backups" / "crucibleforge-private"


def layer_files(root: Path | None = None) -> list[Path]:
    root = root or REPO
    seen: set[Path] = set()
    for pat in LAYER_GLOBS:
        for p in root.glob(pat):
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts:
                seen.add(p)
    return sorted(seen)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(root: Path | None = None) -> dict[str, str]:
    root = root or REPO
    return {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in layer_files(root)}


def _manifest_text(m: dict[str, str]) -> str:
    return "".join(f"{h}  {rel}\n" for rel, h in sorted(m.items()))


def archives(dest: Path | None = None) -> list[Path]:
    d = dest or backup_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{PREFIX}*.tar.gz"), key=lambda p: p.name, reverse=True)


def _archive_manifest(path: Path) -> dict[str, str]:
    with tarfile.open(path, "r:gz") as tf:
        f = tf.extractfile(MANIFEST)
        text = f.read().decode("utf-8") if f else ""
    out = {}
    for line in text.splitlines():
        h, _, rel = line.partition("  ")
        if rel:
            out[rel] = h
    return out


def cmd_status(args) -> int:
    m = manifest()
    if not m:
        print("private layer: empty (a clean clone: no local-only tests or settings)")
        return 0
    total = 0
    for rel, h in sorted(m.items()):
        size = (REPO / rel).stat().st_size
        total += size
        print(f"{h[:16]}  {size:>9}  {rel}")
    newest = archives()
    print(f"\n{len(m)} file(s), {total} bytes")
    if newest:
        same = _archive_manifest(newest[0]) == m
        print(f"newest backup: {newest[0]}  ({'matches the layer' if same else 'DIFFERS from the layer'})")
    else:
        print(f"no backup yet in {backup_dir()} — run: python3 scripts/private_layer.py backup")
    return 0


def do_backup(label: str = "", if_changed: bool = False, keep: int = 20, quiet: bool = False) -> Path | None:
    m = manifest()
    if not m:
        if not quiet:
            print("private layer: empty — nothing to back up")
        return None
    dest = backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    prev = archives(dest)
    if if_changed and prev and _archive_manifest(prev[0]) == m:
        if not quiet:
            print(f"private layer unchanged since {prev[0].name} — no new backup")
        return prev[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{PREFIX}{stamp}{'-' + label if label else ''}.tar.gz"
    out = dest / name
    n = 1
    while out.exists():
        n += 1
        out = dest / f"{PREFIX}{stamp}-{n}{'-' + label if label else ''}.tar.gz"
    tmp = out.with_suffix(".part")
    old = os.umask(0o077)
    try:
        with tarfile.open(tmp, "w:gz") as tf:
            data = _manifest_text(m).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tf.addfile(info, io.BytesIO(data))
            for rel in sorted(m):
                tf.add(REPO / rel, arcname=rel, recursive=False)
        os.chmod(tmp, 0o600)
        tmp.replace(out)
    finally:
        os.umask(old)
        if tmp.exists():
            tmp.unlink()
    # verify what was written before trusting it
    if _archive_manifest(out) != m:
        print(f"private_layer: backup {out} failed verification", file=sys.stderr)
        return None
    for stale in archives(dest)[keep:]:
        stale.unlink()
    if not quiet:
        print(f"backed up {len(m)} file(s) -> {out}")
    return out


def cmd_backup(args) -> int:
    return 0 if do_backup(args.label, args.if_changed, args.keep, args.quiet) or not manifest() else 1


def cmd_list(args) -> int:
    for a in archives():
        print(f"{a.name}  {len(_archive_manifest(a))} file(s)")
    return 0


def _resolve(which: str) -> Path:
    if which == "latest":
        found = archives()
        if not found:
            raise SystemExit(f"private_layer: no backups in {backup_dir()}")
        return found[0]
    p = Path(which).expanduser()
    if not p.exists():
        p = backup_dir() / which
    if not p.exists():
        raise SystemExit(f"private_layer: no such backup: {which}")
    return p


def cmd_restore(args) -> int:
    src = _resolve(args.archive)
    want = _archive_manifest(src)
    have = manifest()
    missing = sorted(r for r in want if r not in have)
    differ = sorted(r for r in want if r in have and have[r] != want[r])
    extra = sorted(r for r in have if r not in want)
    print(f"restore from {src.name}: {len(want)} file(s) — "
          f"{len(missing)} missing here, {len(differ)} differ, "
          f"{len(want) - len(missing) - len(differ)} identical"
          + (f"; {len(extra)} local file(s) not in the backup are left alone" if extra else ""))
    for r in missing:
        print(f"  + {r}")
    for r in differ:
        print(f"  ~ {r}")
    if not (missing or differ):
        return 0
    if not args.go:
        print("dry run — add --go to write these files back (the current layer is backed up first)")
        return 0
    if have and not do_backup("pre-restore", quiet=True):
        print("private_layer: could not back up the current layer — refusing to restore", file=sys.stderr)
        return 1
    root = REPO.resolve()
    with tarfile.open(src, "r:gz") as tf:
        for rel in missing + differ:
            target = (root / rel).resolve()
            if root not in target.parents:
                print(f"private_layer: refusing path outside the repo: {rel}", file=sys.stderr)
                return 1
            f = tf.extractfile(rel)
            if f is None:
                print(f"private_layer: {rel} is not in the archive", file=sys.stderr)
                return 1
            data = f.read()
            if _sha(data) != want[rel]:
                print(f"private_layer: {rel} fails its checksum in the archive", file=sys.stderr)
                return 1
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    print(f"restored {len(missing) + len(differ)} file(s)")
    return 0


def cmd_check(args) -> int:
    """For git hooks after a checkout/merge: warn (never fail) when a file the
    newest backup holds has vanished from the checkout."""
    found = archives()
    if not found:
        return 0
    gone = sorted(r for r in _archive_manifest(found[0]) if not (REPO / r).is_file())
    if gone:
        print(f"\nprivate_layer: WARNING — {len(gone)} local-only file(s) from the newest backup "
              f"are missing from this checkout.\n"
              f"               Inspect: python3 scripts/private_layer.py restore latest\n"
              f"               Restore: python3 scripts/private_layer.py restore latest --go\n",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="list the layer's files with sha256").set_defaults(fn=cmd_status)
    b = sub.add_parser("backup", help="write a timestamped tarball of the layer")
    b.add_argument("--label", default="", help="suffix for the archive name")
    b.add_argument("--if-changed", action="store_true", help="skip when the newest backup already matches")
    b.add_argument("--keep", type=int, default=20, help="archives to keep (default 20)")
    b.add_argument("--quiet", action="store_true")
    b.set_defaults(fn=cmd_backup)
    sub.add_parser("list", help="list backups, newest first").set_defaults(fn=cmd_list)
    r = sub.add_parser("restore", help="put a backup's files back (dry run unless --go)")
    r.add_argument("archive", nargs="?", default="latest", help="'latest', a file name or a path")
    r.add_argument("--go", action="store_true")
    r.set_defaults(fn=cmd_restore)
    sub.add_parser("check", help="warn if files from the newest backup are missing").set_defaults(fn=cmd_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
