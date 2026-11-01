import json
import os


def save_state(path, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, default=str, indent=1, sort_keys=True)


def load_state(path) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
