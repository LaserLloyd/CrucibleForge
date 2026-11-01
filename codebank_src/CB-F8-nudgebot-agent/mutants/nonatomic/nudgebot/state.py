import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


def _enc(o):
    if isinstance(o, datetime):
        return {"$dt": o.isoformat()}
    raise TypeError(f"cannot store {type(o).__name__}")


def _dec(d):
    if set(d) == {"$dt"}:
        return datetime.fromisoformat(d["$dt"])
    return d


def save_state(path, state: dict) -> None:
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, default=_enc, indent=1, sort_keys=True)


def load_state(path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"), object_hook=_dec)
