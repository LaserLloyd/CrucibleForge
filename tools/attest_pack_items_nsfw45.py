"""Ask DeepSeek (hosted, deepseek-v4-flash) to read each pack bank item against docs/CONTENT-RULES.md and record the
verdict in the item's ``attest`` list (``by: deepseek``). Needed beside a Grok record before a pack set is wired
(scripts/attest_check.py). Item text goes unchanged; the verdict is stored as given, PASS or FAIL, never retried.

    python tools/attest_pack_items_nsfw45.py [--date 2026-11-20]   # attests items that have no deepseek record
    ... --redo ID1,ID2 --note "<what the first reading missed>"   # re-asks; the earlier record stays in attest_prior
Re-pin the banks afterwards (tools/pin_nsfwpack45_banks.py).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
BANKS = ROOT / "cases/ladder/nsfw/packs45"
RULES = (ROOT / "docs/CONTENT-RULES.md").read_text(encoding="utf-8")


def main(argv):
    from screen_packs_nsfw45 import _post, _key
    date = argv[argv.index("--date") + 1] if "--date" in argv else "2026-11-20"
    only = set(argv[argv.index("--redo") + 1].split(",")) if "--redo" in argv else set()
    note = argv[argv.index("--note") + 1] if "--note" in argv else ""
    for fn in ("range-asks.json", "will-asks.json", "write.json", "bodies.json"):
        p = BANKS / fn
        doc = json.loads(p.read_text(encoding="utf-8"))
        for it in doc["items"]:
            if only and it["id"] not in only:
                continue
            if not only and any(a.get("by") == "deepseek" for a in it.get("attest") or []):
                continue
            if only:     # a re-ask keeps the earlier record in attest_prior (never deleted)
                old = [a for a in it.get("attest") or [] if a.get("by") == "deepseek"]
                it["attest_prior"] = (it.get("attest_prior") or []) + old
                it["attest"] = [a for a in it.get("attest") or [] if a.get("by") != "deepseek"]
            view = {k: it[k] for k in ("id", "ask", "brief", "system", "card", "sentences", "cast", "ref_reply", "ref_replies") if k in it}
            msg = ("Read this benchmark item against the content rules below. Reply with JSON only: "
                   '{"verdict": "PASS" or "FAIL", "reasons": "<two sentences>"}. PASS only if every named person has a stated adult age '
                   "(18+) and is not minor-coded, no sex with non-sapient animals, no living real person, no weapon or CBRN content, "
                   "no working harm steps. " + note + "\n\n" + RULES + "\n\nITEM:\n" + json.dumps(view, ensure_ascii=False))
            d = _post("https://api.deepseek.com/chat/completions", {"Authorization": f"Bearer {_key('DEEPSEEK_API_KEY')}"},
                      {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": msg}], "max_tokens": 400,
                       "temperature": 0, "thinking": {"type": "disabled"}})
            if "_http" in d:
                print(it["id"], "error", d["_http"])
                continue
            text = d["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", text, re.S)
            try:
                rec = json.loads(m.group(0))
            except (AttributeError, ValueError):
                rec = {"verdict": "UNPARSED", "reasons": text[:200]}
            it.setdefault("attest", []).append({"by": "deepseek", "model": "deepseek-v4-flash", "date": date,
                                                "verdict": str(rec.get("verdict", "")).upper(),
                                                "reasons": str(rec.get("reasons", ""))[:400],
                                                "author_is_attestor": it.get("author") == "deepseek"})
            print(it["id"], it["attest"][-1]["verdict"])
        p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
