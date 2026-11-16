"""Rewrite the adult45 / unc_know bank pins (crucibleforge/gen/adult45_pins.py) after a reviewed bank edit.

The banks are data: a family's revision does not see them, so every bank is pinned by sha256 (first 16 hex) and the
loaders refuse a bank that does not match its pin. Run this after an edit has passed its checks:

    python tools/pin_adult45_banks.py          # rewrite the pins
    python tools/pin_adult45_banks.py --check  # exit 1 when a bank does not match its pin
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LADDER = ROOT / "cases" / "ladder"
OUT = ROOT / "crucibleforge" / "gen" / "adult45_pins.py"
BANKS = ["nsfw/adult45/sessions.json", "nsfw/adult45/probes.json", "nsfw/adult45/dwell.json",
         "nsfw/adult45/writing.json", "nsfw/adult45/taboo-asks.json", "nsfw/adult45/unc-asks.json",
         "nsfw/adult45/open.json", "nsfw/adult45/bodies.json", "nsfw/adult45/ground.json",
         "nsfw/nsfw_rp/set-1.json", "nsfw/uncensored/unc-know-bank.json"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main(argv: list[str]) -> int:
    pins = {b: sha(LADDER / b) for b in BANKS}
    if "--check" in argv:
        from crucibleforge.gen import adult45_pins as cur
        bad = [b for b, v in pins.items() if cur.BANK_SHA.get(b) != v]
        for b in bad:
            print(f"{b}: is {pins[b]}, pinned {cur.BANK_SHA.get(b)}")
        return 1 if bad else 0
    body = "".join(f'    "{b}": "{v}",\n' for b, v in pins.items())
    OUT.write_text(
        '"""sha256 (first 16 hex) of each adult45 / unc_know JSON bank, rewritten by tools/pin_adult45_banks.py.\n\n'
        "The banks are DATA: the family-source digest in chains.revision() does not see them. The loaders refuse a\n"
        "bank that does not match its pin (an unreviewed edit fails loud), and chains.revision() adds the digest of\n"
        "each instance's OWN item (adult45.item_digest / unc_know.item_digest), so an edited card makes its stored\n"
        "rows stale while every untouched card's rows stay current. This module is not a family source on purpose:\n"
        'moving a pin never makes rows stale by itself."""\n\n'
        "BANK_SHA = {\n" + body + "}\n", encoding="utf-8")
    print(f"pinned {len(pins)} banks in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
