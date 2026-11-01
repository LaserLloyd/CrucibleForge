"""Importing a folder of photos into the library."""
import shutil
from datetime import timezone
from pathlib import Path

from . import catalog
from .hashing import sha256_file
from .paths import free_name, is_photo
from .sidecar import parse_taken, read_sidecar


def import_dir(src, library, conn) -> dict:
    src, library = Path(src), Path(library)
    out = {"imported": [], "skipped": [], "ignored": []}
    for p in sorted(src.iterdir()):
        if p.name.endswith(".json") or not p.is_file():
            continue
        side = p.with_name(p.name + ".json")
        if not is_photo(p) or not side.exists():
            out["ignored"].append(p.name)
            continue
        size = p.stat().st_size
        digest = sha256_file(p)
        if catalog.seen(conn, digest):
            out["skipped"].append(p.name)
            continue
        taken = parse_taken(read_sidecar(side))
        tu = taken.astimezone(timezone.utc)
        folder = library / f"{tu:%Y}" / f"{tu:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        name = free_name(folder, p.name)
        shutil.copy2(p, folder / name)
        rel = (folder / name).relative_to(library).as_posix()
        catalog.add(conn, p.name, size, rel, taken.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), digest)
        out["imported"].append(rel)
    return out
