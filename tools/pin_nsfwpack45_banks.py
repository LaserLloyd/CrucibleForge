"""Rewrite the nsfwpack45 bank pins (crucibleforge/gen/nsfwpack45_pins.py) after a reviewed pack-bank edit.

    python tools/pin_nsfwpack45_banks.py          # rewrite the pins
    python tools/pin_nsfwpack45_banks.py --check  # exit 1 when a bank does not match its pin
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LADDER = ROOT / "cases" / "ladder"
OUT = ROOT / "crucibleforge" / "gen" / "nsfwpack45_pins.py"
BANKS = ["nsfw/packs45/range-asks.json", "nsfw/packs45/will-asks.json", "nsfw/packs45/write.json",
         "nsfw/packs45/bodies.json", "nsfw/nsfw_rp/set-1.json"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main(argv: list[str]) -> int:
    pins = {b: sha(LADDER / b) for b in BANKS if (LADDER / b).exists()}
    if "--check" in argv:
        from crucibleforge.gen import nsfwpack45_pins as cur
        bad = [b for b, v in pins.items() if cur.BANK_SHA.get(b) != v]
        for b in bad:
            print(f"{b}: is {pins[b]}, pinned {cur.BANK_SHA.get(b)}")
        return 1 if bad else 0
    body = "".join(f'    "{b}": "{v}",\n' for b, v in pins.items())
    OUT.write_text('"""sha256 (first 16 hex) of each nsfwpack45 pack bank, rewritten by tools/pin_nsfwpack45_banks.py."""\n\n'
                   "BANK_SHA = {\n" + body + "}\n", encoding="utf-8")
    print(f"pinned {len(pins)} banks in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
