from pathlib import Path

PHOTO_EXT = {".jpg", ".jpeg", ".heic"}


def is_photo(p: Path) -> bool:
    return p.suffix.lower() in PHOTO_EXT


def free_name(folder: Path, name: str) -> str:
    """`name`, or `<stem>-N<ext>` for the first N that does not exist yet."""
    if not (folder / name).exists():
        return name
    stem, ext = Path(name).stem, Path(name).suffix
    n = 1
    while (folder / f"{stem}-{n}{ext}").exists():
        n += 1
    return f"{stem}-{n}{ext}"
