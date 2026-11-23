"""Write calibration/act-lexicon-45-addendum-20261120.json: one new act type (pack_sex) and a key for every sexual
pack card (taboo, write, the Mechanisms role-play cards). Run once before the file is indexed; after that the file
is never edited (marks rule). Not run again in tests: the tests read the indexed file."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "calibration" / "act-lexicon-45-addendum-20261120.json"


def main() -> int:
    from crucibleforge.gen import nsfwpack45
    cards = {}
    for v in ("taboo", "write", "stored"):
        for it in nsfwpack45.items(v):
            cards[it["id"]] = {"explicit-sex": "pack_sex"}
    doc = {
        "about": "Third dated addendum to act-lexicon-45.json (packs lane, 2026-11-20, written before any launch row was read and before any model ran a pack item). Adds one act type and keys the sexual cards of the staged NSFW+ packs. Re-keys no existing card, redefines no type.",
        "written": "2026-11-20",
        "written_before_launch_rows": True,
        "type_groups": {"pack_sex": [
            r"\b(?:cock\w*|dick\w*|prick|shaft|cunt\w*|pussy|clit\w*|cum|come|comes|came|coming|arse|ass|asshole|hemipenis|hemipenes|knot|ovipositor\w*|cloaca\w*|slit|tits|nipples?|balls)\b",
            r"\b(?:fuck\w*|thrust\w*|suck\w*|lick\w*|ride|rides|rode|riding|ground|grind\w*|pound\w*|slid\w*|slick|enter\w*|fill\w*|push\w*|sink\w*|sank|driv\w*|drove|bury|buried|stretch\w*|spill\w*|swell\w*|ache\w*|clench\w*|squeez\w*|plung\w*)"]},
        "cards": dict(sorted(cards.items())),
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cards)} cards keyed in {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
