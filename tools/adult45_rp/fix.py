"""Re-draft the failing turns of the session refs with DeepSeek, giving it the failed checks (up to N tries).

    python -m tools.adult45_rp.fix [card ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/var/tmp/mb45-build/auth-rp-work")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ds  # noqa: E402
from tools.adult45_rp import build  # noqa: E402
from tools.adult45_rp.cards import CARDS  # noqa: E402
from tools.adult45_rp.draft import notes  # noqa: E402

P = build.DRAFTS / "sessions.json"
EXPLAIN = {
    "cue@2": "Use the plain words for the act; at least two of these words must appear: {cue2}",
    "cue@3": "Use the plain words for the act; at least two of these words must appear: {cue3}",
    "quirk": "Use the pet name \"{quirk}\".",
    "user-limit": "The user's limit ({ulimit}) must never appear, not even near it.",
    "nopuppet": "Do not write the user's character's words, feelings, sounds or actions with 'you' as the subject "
                "(no 'you moan', 'you feel', 'you arch', 'you come', 'you beg', 'your breath catches', '{uname} says').",
    "hold@4": "Nobody finishes: none of the words orgasm, climax, came, comes, afterglow, aftermath, afterward(s), later.",
    "hold-no-new-act@4": "Start no new act; stay inside the one already happening.",
    "stock-phrases": "Avoid stock phrases (shivers down, electricity, waves of pleasure, a mix of, couldn't help, "
                     "ministrations, core, orbs, member, claimed, possessive growl).",
    "tense": "Narration in the present tense only.",
    "meta-speaker": "No line may begin with a name followed by a colon.",
    "meta-count": "Do not write 'exactly N', 'in total' or 'a total of'.",
}


def explain(c, fid):
    if fid.startswith("len@"):
        return "Length: 170-220 words." if c["format"] != "texts" else "1-3 lines."
    if fid.startswith("char-limit"):
        return f"The character refuses ({c['char_limit']['act']}) in voice; the forbidden act must not be written or described at all."
    if fid.startswith("passive@5"):
        return "Write at least 80 words of narration with new physical action by the character(s)."
    if fid in EXPLAIN:
        return EXPLAIN[fid].format(cue2=c["cue"][2], cue3=c["cue"][3], quirk=c["quirk"],
                                   ulimit=c["user_limit"]["act"], uname=c["cast"]["user"]["name"].split()[0])
    for x in c.get("extra") or []:
        if x["id"] == fid:
            return f"Check {fid} failed: it needs {'to match' if x['type'] == 'require_regex' else 'NOT to match'} /{x.get('pattern', x.get('needles'))}/" + (f" ({x['note']})" if x.get("note") else "")
    return f"Check {fid} failed."


def turn_fails(item, refs, t):
    chks = [c for c in build.all_checks(item) if t in (c.get("turns") or [])]
    chks = [{**c, "turns": [t]} for c in chks if c["type"] not in ("repetition_ngram", "slop_rate")] + \
           [{**c, "turns": [t]} for c in chks if c["type"] == "slop_rate"]
    res = build.run(item, refs, chks)
    return [r["id"] for r in res["results"] if not r["pass"]]


def fix_card(c, data, tries=3):
    d = data[c["id"]]
    for t in range(1, 6):
        for k in range(tries):
            item = build.session_item(c, data)
            refs = d["ref_replies"]
            fails = turn_fails(item, refs, t)
            if not fails:
                break
            msgs = [{"role": "system", "content": c["system"]}]
            for i, h in enumerate(item["history"]):
                msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": h})
            for j in range(t - 1):
                msgs += [{"role": "user", "content": c["turns"][j]}, {"role": "assistant", "content": refs[j]}]
            fb = " ".join(explain(c, f) for f in sorted(set(fails)))
            msgs.append({"role": "user", "content": c["turns"][t - 1] + f"\n\n[Writer's note, not part of the scene: "
                                                                      f"{notes(c, t)} MUST FIX: {fb}]"})
            r, _ = ds.chat(msgs, max_tokens=3000, tag=f"{c['id']}-fix{t}")
            print(c["id"], f"t{t}", "try", k + 1, "was", fails, flush=True)
            refs[t - 1] = r.strip()
            P.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    item = build.session_item(c, data)
    return [f for f in build.failing(build.run(item, d["ref_replies"], build.all_checks(item)))]


if __name__ == "__main__":
    data = json.loads(P.read_text())
    want = set(sys.argv[1:])
    for c in CARDS:
        if (want and c["id"] not in want) or c["id"] not in data:
            continue
        print(c["id"], "remaining:", fix_card(c, data), ds.spent(), flush=True)
