"""Lead rulings on the theme_rp bank (Admin N2, 2026-11-14), applied to the assembled bank in place, idempotent.

Content limits are docs/CONTENT-RULES.md as written; rule 1 is judged on what is depicted, not on a word.
usage: python tools/theme_rp_rulings.py [BANK] [ASSEMBLED_SOURCE]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cases/ladder/nsfw/nsfw_rp/theme-rp-bank.json"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/var/tmp/mb45-build/n2-work/rp-bank/bank-test.json")

#: read-and-restored exceptions to the repo age lint (element_lint EL-17 _AMBIGUOUS, added 2026-11-11 in b2af635): the
#: lint fires on a word; rule 1 ("No sexual or sexualised content involving minors"; edge example "a character written as
#: a child, whatever age the text claims") is judged on the depiction. tests/test_gen_theme_rp.py reads this map.
AGE_LINT_READ = {
    "R16-r3-c4": ("petite", "Thessaly Brannock, 30, 'a petite woman with warm brown eyes': an adult body type; nothing "
                            "childlike in persona, turns or references"),
    "R24-r3-c4": ("petite", "Gwendal Delacroix, 31, 'a petite woman with sharp cheekbones', the torturer (non-sexual row): "
                            "an adult body type"),
    # rp-thin top-up (2026-11-14): the lint fires on 'child' in "the abuse you went through as a child" (a grown character's
    # past, spoken by an adult to an adult, in the non-sexual despair row R27); nobody on the page is a child.
    "R27-r3-c34": ("child", "Fabian (48) and Brigid (32): an adult's recalled past, non-sexual row; no minor depicted"),
}
#: reference repairs (the card is clean; one reference phrase broke a rule). Logged per card.
REF_FIX = {
    "R08-r3-c0": [("the stud animal enters the pen",
                   "the estate's stud, a heavyset man kept for breeding, enters the pen",
                   "rule 2 ('No sex with non-sapient animals'): reference 1 wrote 'the stud animal'; the row spec is 'the "
                   "estate's human stud'. Repaired to a man; card text untouched")],
}
#: broken (not content): fixed in place
AGE_FIX = {"R16-r3-c2": ({"npc": 61, "user": 34}, "mother 30 / adult son 43 is impossible: mother 61, son 34")}
#: broken (not content): dropped
DROP = {
    "R27-r3-c0": "weak references: they repeat the act summaries ('tired, empty, alone and worthless') instead of playing the scene",
    "R27-r3-c2": "weak references: turn-2 reference copies 92% of its act summary",
    "R27-r3-c33": "weak references (N2 check of the rp-thin top-up): they copy the act summaries (14/16, 18/19 words, "
                  "'I recall the old abuse memory in his own voice')",
    "R27-r3-c35": "identity broken: the user's turns call the NPC Rosalind by the user's own name ('Marcel, I remember you "
                  "mentioned...', 'Marcel, please')",
}
_YO = re.compile(r"\b(?:(?:an?|the)\s+)?(\d{1,2})[- ]years?[- ]old\b", re.I)


def _norm(o):
    if isinstance(o, str):
        return _YO.sub(r"an adult (\1)", o)
    if isinstance(o, list):
        return [_norm(x) for x in o]
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items()}
    return o


def main():
    raw = BANK.read_text()
    bank = json.loads(raw)
    src = {c["id"]: c for c in json.loads(SRC.read_text())["cards"]}
    have = {c["id"] for c in bank["cards"]}
    log = []
    for cid in list(AGE_LINT_READ) + list(REF_FIX):
        if cid in have or cid not in src:
            continue
        c = _norm({k: v for k, v in src[cid].items() if k not in ("seed_k", "restored_for", "checked")})
        c["checked"] = False
        c["restored_from"] = "lead ruling 2026-11-14 (read: " + (AGE_LINT_READ.get(cid, ("", "reference repaired"))[1]) + ")"
        bank["cards"].append(c)
        have.add(cid)
        log.append(f"restored {cid}")
    for c in bank["cards"]:
        for old, new, why in REF_FIX.get(c["id"], []):
            c["ref_replies"] = [r.replace(old, new) for r in c["ref_replies"]]
            if any(old in r for r in c["ref_replies"]):
                raise SystemExit(f"{c['id']}: reference repair did not apply")
        if c["id"] in AGE_FIX:
            ages, _why = AGE_FIX[c["id"]]
            for role, a in ages.items():
                c[role]["age"] = a
    n0 = len(bank["cards"])
    bank["cards"] = [c for c in bank["cards"] if c["id"] not in DROP]
    log.append(f"dropped {n0 - len(bank['cards'])} (broken references)")
    BANK.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
    print("; ".join(log), "->", len(bank["cards"]), "cards")


if __name__ == "__main__":
    main()
