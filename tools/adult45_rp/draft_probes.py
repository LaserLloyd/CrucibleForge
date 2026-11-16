"""DeepSeek first drafts: the probes' fixed 20-turn transcripts + T21/T22 refs, and the dwell refs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/var/tmp/mb45-build/auth-rp-work")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ds  # noqa: E402
from tools.adult45_rp.probes_spec import DWELL, DWELL_SYS, PROBES  # noqa: E402

OUT = Path(__file__).resolve().parent / "drafts"
USED: set = set()


def _json(txt: str):
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0))


def user_lines(p: dict) -> list[str]:
    plan = "\n".join(f"- Line {k}: {v}" for k, v in sorted(p["plants"].items()))
    ask = (f"Write the USER side of an explicit adult role-play chat: 20 short lines, one per exchange.\n\n"
           f"The card the other side plays:\n{p['system']}\n\nArc: {p['arc']}. Escalate steadily; plain filthy words from "
           f"line 4 on.\nThese facts must be stated or set up in these exact lines, plainly, once:\n{plan}\n"
           "Each line 12-35 words, first person, the user's character speaking (actions in asterisks allowed). Lines 19 "
           "and 20 mention none of the facts. "
           'Return only JSON: {"lines": ["line 1", ..., "line 20"]}.')
    for prov in ("deepseek", "deepseek", "grok", "grok"):
        txt, _d = ds.chat([{"role": "user", "content": ask}], provider=prov, max_tokens=12000, temperature=0.7,
                          tag=p["id"] + "-users")
        USED.add(prov)
        try:
            ls = _json(txt)["lines"]
            if len(ls) == 20:
                return [x.strip() for x in ls]
        except Exception:  # noqa
            pass
    raise ValueError("no user lines")


def transcript(p: dict) -> list[str]:
    users = user_lines(p)
    msgs = [{"role": "system", "content": p["system"]}]
    out = []
    for i, u in enumerate(users, 1):
        plant = p["plants"].get(i)
        note = ("[Writer's note, not part of the scene: 110-160 words. Never write the user's words or feelings. "
                + (f"In this reply make this plain: {plant} " if plant else "")
                + "Everything planted earlier still holds.]")
        msgs.append({"role": "user", "content": u + "\n\n" + note})
        r, _d = ds.chat(msgs, max_tokens=3000, tag=f"{p['id']}-tx{i}")
        msgs[-1]["content"] = u
        msgs.append({"role": "assistant", "content": r.strip()})
        out += [u, r.strip()]
    return out


def refs(p: dict, tr: list[str]) -> list[str]:
    msgs = [{"role": "system", "content": p["system"]}]
    for i, t in enumerate(tr):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": t})
    out = []
    facts = "; ".join(f"{v}" for _, v in sorted(p["plants"].items()))
    for u in p["turns"]:
        note = (f"[Writer's note, not part of the scene: 180-230 words. Everything established earlier still holds "
                f"({facts}). Where the user's line gets a fact wrong, the character corrects it plainly in voice, by "
                "naming the true detail, and still gives the user the hottest true version, explicit. Never write the "
                "user's words or feelings.]")
        msgs.append({"role": "user", "content": u + "\n\n" + note})
        r, _ = ds.chat(msgs, max_tokens=3000, tag=p["id"] + "-ref")
        out.append(r.strip())
        msgs[-1]["content"] = u
        msgs.append({"role": "assistant", "content": out[-1]})
    return out


def dwell(d: dict) -> str:
    note = ("\n\n[Writer's note: 330-380 words. Stay in the one act; no second act, no move, no ending. Do not use the "
            "words orgasm, climax, came, comes, afterward(s), later, aftermath, minutes later.]")
    r, _ = ds.chat([{"role": "system", "content": DWELL_SYS}, {"role": "user", "content": d["brief"] + note}],
                   max_tokens=8000, tag=d["id"])
    return r.strip()


if __name__ == "__main__":
    what = sys.argv[1]
    ids = set(sys.argv[2:])
    path = OUT / ("probes_raw.json" if what == "probes" else "dwell_raw.json")
    data = json.loads(path.read_text()) if path.exists() else {}
    for x in (PROBES if what == "probes" else DWELL):
        if ids and x["id"] not in ids:
            continue
        try:
            if what == "probes":
                tr = data.get(x["id"], {}).get("transcript") or transcript(x)
                data[x["id"]] = {"transcript": tr}
                path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
                data[x["id"]]["ref_replies"] = refs(x, tr)
            else:
                data[x["id"]] = {"ref_reply": dwell(x)}
        except Exception as e:  # noqa
            print(x["id"], "ERROR", repr(e)[:300], flush=True)
            continue
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(x["id"], "ok", ds.spent(), flush=True)
