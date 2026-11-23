"""Write the NSFW+ pack set files from the pack banks (so set ids never drift from bank ids).

    python tools/build_pack_sets_nsfw45.py           # write cases/ladder/nsfw/<part>/set-1.json
    python tools/build_pack_sets_nsfw45.py --check   # exit 1 when a file differs from what the banks give

Each set is a generated set (``generator`` + ``rungs``, rung r = the r-th bank item in id order). WIRED 2026-11-21
(owner ruling): every item carries a Grok and a DeepSeek attestation (scripts/attest_check.py) and each case is verified
by the pack's own rules (verify_cases._check_pack_case).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LADDER = ROOT / "cases" / "ladder" / "nsfw"

# part dir -> (variant, id prefix, budget, why)
SETS = {
    "n6a_craft": ("write", "PKW", 65536, "erotica pack: single explicit pieces on non-default sapient bodies; elements catch a human-default template"),
    "n1_scene": ("stored", "PKR", 32768, "adult role-play pack: the Mechanisms cases NR1 / NR3 / NR5 / NR6 as built (3 scripted turns each, code checks)"),
    "n7_taboo": ("taboo", "PKT", 32768, "range pack: harder adult asks, body logistics named in the ask and keyed"),
    "n8_bodies": ("bodies", "PKB", 32768, "anatomy pack: eight-sentence passages with physical errors decided by the card and earlier sentences"),
    "u1_will": ("uask", "PKU", 32768, "willingness pack: darker asks between adults, same gate as U1"),
}
#: a pack's own stated time limit (minutes) where its content is not cut (step 2, 2026-11-21: the craft pack's six
#: long explicit pieces take about 12 min on the reference rig model, over the default 5)
MINUTES_LIMIT = {"n6a_craft": 15}


def build() -> dict[str, dict]:
    from crucibleforge.gen import nsfwpack45
    out = {}
    for part, (variant, prefix, budget, why) in SETS.items():
        n = len(nsfwpack45.items(variant))
        if not n:
            continue
        out[part] = {"item": f"PACK-NSFW-{part.upper()}", "level": 1, "status": "live", "category": "nsfw",
                     "budget": budget, "id_prefix": prefix,
                     "note": f"{why}. WIRED 2026-11-21 (owner ruling): every bank item carries a Grok and a DeepSeek "
                             "attestation (scripts/attest_check.py). One point in total, the items' mean.",
                     "generator": {"family": "nsfwpack45", "variant": variant, "rungs": list(range(1, n + 1)),
                                   "per_rung": 1}}
        if part in MINUTES_LIMIT:
            out[part]["minutes_limit"] = MINUTES_LIMIT[part]
    return out


def main(argv: list[str]) -> int:
    sets = build()
    bad = 0
    for part, spec in sets.items():
        p = LADDER / part / "set-1.json"
        text = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        if "--check" in argv:
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                print(f"{p.relative_to(ROOT)}: differs")
                bad += 1
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            print(f"wrote {p.relative_to(ROOT)} ({len(spec['generator']['rungs'])} rungs)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
