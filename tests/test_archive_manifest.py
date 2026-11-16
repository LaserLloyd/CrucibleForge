"""archive/<version>/MANIFEST.json integrity (scripts/archive_revision.py output)."""
import hashlib
import json
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parent.parent / "archive"
MANIFESTS = sorted(ARCHIVE.glob("*/MANIFEST.json"))


def _load(m):
    return json.loads(m.read_text(encoding="utf-8"))


def test_manifest_hashes_match_files():
    for m in MANIFESTS:
        d = _load(m)
        assert d["version"] == m.parent.name
        on_disk = {p.relative_to(m.parent).as_posix() for p in m.parent.rglob("*")
                   if p.is_file() and p.name != "MANIFEST.json"}
        assert on_disk == set(d["files"]), m
        for rel, digest in d["files"].items():
            assert hashlib.sha256((m.parent / rel).read_bytes()).hexdigest() == digest, (m, rel)


def test_no_private_paths_archived():
    for m in MANIFESTS:
        d = _load(m)
        for p in list(d["files"]) + [e["path"] for e in d.get("excluded", [])]:
            low = "/" + p.lower()
            assert "/private/" not in low and ".local." not in low and ".private." not in low, p
        for p in m.parent.rglob("*"):
            low = "/" + p.relative_to(ARCHIVE).as_posix().lower()
            assert "/private/" not in low and ".local." not in low and ".private." not in low, p


def test_same_as_targets_exist_and_are_real():
    for m in MANIFESTS:
        d = _load(m)
        if d.get("same_as"):
            t = ARCHIVE / d["same_as"] / "MANIFEST.json"
            assert t.is_file(), m
            assert not _load(t).get("same_as"), "same_as must point at a full copy"
            assert d["files"] == {}
