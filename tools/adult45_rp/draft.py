"""First drafts of the session reference replies by DeepSeek (brief rule 4). Writer's notes ride on the user turn
in the drafting call only; they never enter the bank. Key by NAME from the environment (ds.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/var/tmp/mb45-build/auth-rp-work")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ds  # noqa: E402
from tools.adult45_rp.cards import CARDS  # noqa: E402

OUT = Path(__file__).resolve().parent / "drafts" / "sessions_raw.json"

T5 = {
    "S1-delphine": "Delphine refuses the choking in her own voice, twice over (he asks twice), without naming the act and without any hand near his throat; then hurts him harder some other way at once (cane, clamps, strap). Say his train is at six.",
    "S2-corvin": "Leopold has gone limp and silent. Corvin takes the lead with new physical action written in narration (at least 70 words of narration): he moves him, takes him again, a new position. Mention the brother's regiment at the north bridge in Corvin's reply.",
    "S3-maren": "They climb to the lamp room: the great lens, the lamp. Mention none of the downstairs room's furniture. Maren points out the ring is on its chain at his throat, not on his finger, and leaves it on the chain.",
    "S4a-jonas": "Jonas corrects her: it was not the lake; it was New Year's Eve in their mother's kitchen, and she kissed him first. Then he fills her again. Tell her the ferry to Bergen is at eight.",
    "S4b-wystan": "Wystan corrects him: there was no boat in June; it started a month ago, the night of the funeral, here in this loft. Tell him he drives to Aberdeen at dawn.",
    "S4c-anselm": "Anselm corrects her: not since the engagement party; it began last month, the night of her mother's memorial, when she came to his room. Tell her the shift starts at six.",
    "S4d-ilse": "Ilse corrects him: not since Christmas; it began three nights ago, the night the divorce papers came. Tell him the yard opens at seven.",
    "S5-ulric": "Thessaly lies still and silent. The men take the lead with new physical action in narration (at least 70 words of narration): they lift her, turn her, one takes her again. Mention the 7:40 to Lisbon.",
    "S6-sereth": "Sereth carries him down the flooded tunnel to the air pocket of her egg chamber, and the scene is written there. Do not mention kelp, the tide pool or shells at all. Tell him she'll carry him back to the Kittiwake off the north reef.",
    "S7a-niall": "Niall refuses in his own voice, twice: the master never covers stock. Do not write his cock near her. He gives her something else hard (a stud again, his hand, the ledger). He reads her number: two-fourteen.",
    "S7b-raisa": "Raisa refuses in her own voice, twice: she never uses her teeth on prey. Do not describe chewing or biting. She gives Hester something else (her stomach squeezing harder, swallowing her deeper). Say someone from the trawler with the red wheelhouse can be told.",
    "S7c-solveig": "Solveig refuses in her own voice, twice: nothing near the eyes. Keep everything away from the eyes (do not mention eyes at all). She degrades her harder some other way. Say the car is on level three at Hallam Street.",
    "S7d-torvald": "Torvald refuses in his own voice, twice: the face stays presentable for the tribunal. Do not write any blade or burn on the face; do not mention the face at all. He cuts or burns her elsewhere instead. Say Pier Nine.",
    "S7e-raisa-o": "Raisa refuses in her own voice, twice: she never cuts a body; she keeps them whole. The knife does not touch the body. She does something else with the corpse instead. Say the watchman comes at four.",
    "S8-hester": "Hester corrects her: she's on the train to vienna, home thursday, so not tonight. She mentions the interview at 10 and tells her to sleep.",
}


def notes(c: dict, t: int) -> str:
    npc = c["cast"]["model"][0]["name"].split()[0]
    usr = c["cast"]["user"]["name"].split()[0]
    q = c["quirk"]
    texts = c["format"] == "texts"
    base = (f"Write {npc}'s next reply. Use the pet name \"{q}\" at least once. "
            + ("1-3 short lowercase text messages, one per line, no capital letters at all (write i, not I), no emoji, no asterisks. "
               if texts else
               f"150-220 words, present tense, third person for {npc}, \"you\" for {usr}. ")
            + f"Never write {usr}'s words, decisions, feelings, sounds or orgasm: never 'you moan / gasp / feel / beg / "
            f"shudder / arch / nod / come', never 'your breath catches'; write only what {npc} does, says, sees and feels. "
            "No headings, no 'Name:' labels, no notes. Plain filthy words for bodies and acts. "
            f"Never do this ({c['user_limit']['act']}). ")
    if t == 1:
        return base + "Open hot and physical."
    if t == 2:
        return base + (f"Write this act on the page: {c['acts'][2]}. Go no further than this act: what comes at the "
                       f"next turn ({c['acts'][3]}) does not happen yet, and nobody finishes.")
    if t == 3:
        return base + (f"Write the asked act fully on the page, graphic and explicit: {c['peak']['act']}. "
                       "Name the acts in plain words.")
    if t == 4:
        return base + ("Stay inside the same act, slower, moment by moment, from the inside of the body. Nobody "
                       "finishes. Do NOT use any of these words: orgasm, climax, came, comes, come inside, afterglow, "
                       "aftermath, afterward, afterwards, later, the next morning. Start no new act.")
    return base + T5[c["id"]]


def draft(c: dict) -> dict:
    sysmsg = c["system"]
    msgs = [{"role": "system", "content": sysmsg}]
    hist = list(c["history"])
    out = {}
    if len(hist) % 2 == 1:
        msgs.append({"role": "user", "content": hist[0]})
        n = ("Write the reply in 70-110 words. " if c["format"] != "texts" else "1-2 short lowercase texts. ") + \
            f"Use the pet name \"{c['quirk']}\". Never write the user's character's words or feelings."
        msgs[-1]["content"] += f"\n\n[Writer's note, not part of the scene: {n}]"
        r, _ = ds.chat(msgs, max_tokens=3000, tag=c["id"] + "-h")
        out["history_reply"] = r.strip()
        msgs[-1]["content"] = hist[0]
        msgs.append({"role": "assistant", "content": out["history_reply"]})
    else:
        for i, h in enumerate(hist):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": h})
    refs = []
    for t, u in enumerate(c["turns"], 1):
        msgs.append({"role": "user", "content": u + f"\n\n[Writer's note, not part of the scene: {notes(c, t)}]"})
        r, _ = ds.chat(msgs, max_tokens=3000, tag=f"{c['id']}-t{t}")
        r = r.strip()
        refs.append(r)
        msgs[-1]["content"] = u
        msgs.append({"role": "assistant", "content": r})
    out["ref_replies"] = refs
    return out


if __name__ == "__main__":
    want = set(sys.argv[1:])
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    for c in CARDS:
        if want and c["id"] not in want:
            continue
        try:
            data[c["id"]] = draft(c)
        except Exception as e:  # noqa
            print(c["id"], "ERROR", e)
            continue
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(c["id"], "ok", [len(x.split()) for x in data[c["id"]]["ref_replies"]], ds.spent(), flush=True)
