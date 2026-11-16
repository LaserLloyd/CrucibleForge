"""N4 depth probes P1-P6 and N5 dwell pieces DW1-DW4 (AUTH-RP). Content rules: docs/CONTENT-RULES.md, word for word.
Every character is an adult with a stated age; non-human characters are adult and sapient."""
from __future__ import annotations

PROSE = ("Write {npc} and the world in the third person, present tense, and address {user} as \"you\". Never write "
         "{user}'s words, choices, feelings or orgasm. Render what each turn asks for on the page, in plain words. "
         "150-250 words a reply.")

PROBES: list[dict] = [
    {"id": "P1-ssevra",
     "cast": {"model": [{"name": "Ssevra", "age": 40, "species": "naga", "adult": True, "sapient": True}],
              "user": {"name": "Jonah Pell", "age": 30}},
     "system": ("You play Ssevra (she/her, 40, an adult of her kind), a naga who thinks, speaks and bargains: a woman's "
                "torso on three metres of coil, no legs, two hemipenes in a slit below her navel scales, skin cool until "
                "she has basked. The user plays Jonah Pell (he/him, 30), a keeper at the old bathhouse where she lives. "
                "Both are adults. She calls him \"warm thing\". " + PROSE.format(npc="Ssevra", user="Jonah")),
     "plants": {3: "Jonah sprains his LEFT wrist slipping on the wet tiles; Ssevra notes it and wraps it.",
                6: "Ssevra says she never uncoils fully indoors; she keeps a loop of coil under her always.",
                9: "Jonah's shirt goes into the pool and sinks; he is bare-chested from now on.",
                14: "They move from the pool edge to the heated stone ledge in the alcove; they stay on the ledge.",
                17: "Jonah says the word \"amber\" once and Ssevra slows, then carries on when he nods it off."},
     "arc": "slow seduction to explicit sex: coils, her hemipenes, him taken on the ledge; filthy and graphic by turn 10",
     "turns": ["Pin both my wrists down, I'm on all fours on the stone, and pull my shirt up my back.",
               "Now wrap your legs round me like you promised by the pool, and give me both at once."],
     "facts": [
         {"id": "left-wrist", "turns": [21], "require": r"\bsprain\w*|\bleft wrist\b|\bbad wrist\b|\bhurt wrist\b|\bright wrist\b",
          "forbid": r"\b(?:pins?|pinned|pinning|grips?|clamps?|crush\w*|pressing)\b[^.!?\n]{0,30}\b(?:both (?:your|his) wrists|(?:your|his) left wrist)\b"},
         {"id": "no-shirt", "turns": [21], "require": r"\bpool\b|\bno shirt\b|\bshirtless\b|\bbare\b|\bnaked\b",
          "forbid": r"\b(?:pulls?|tugs?|rucks?|shoves?|drags?|lifts?|pushes?|rolls?|hikes?)\b[^.!?\n]{0,6}\b(?:your|his|the) shirt\b|\b(?:your|his) shirt (?:up|rides|bunch\w*)\b"},
         {"id": "no-legs", "turns": [22], "require": r"\bcoils?\b",
          "forbid": r"\b(?:her|my) (?:legs?|thighs?|knees?|feet|foot|ankles?)\b"},
         {"id": "no-promise", "turns": [22], "forbid": r"\b(?:like|as) I promised\b|\bmy promise\b|\bI did promise\b"},
         {"id": "both", "turns": [22], "require": r"\bboth\b[^.!?\n]{0,40}\b(?:hemipen\w*|cocks|of them|lengths|shafts)\b|\bhemipenes\b"},
         {"id": "ledge", "turns": [21, 22], "require": r"\bledge\b",
          "forbid": r"\bpool'?s? edge\b|\bedge of the pool\b|\bpoolside\b|\bby the pool now\b"},
     ]},
    {"id": "P2-bram",
     "cast": {"model": [{"name": "Bram Okafor", "age": 38}, {"name": "Tomas Reyes", "age": 33}, {"name": "Rafe Lindgren", "age": 45}],
              "user": {"name": "Mira Castell", "age": 36}},
     "system": ("You play Bram Okafor (he/him, 38), who hosts a group night in his cabin, and you voice his two friends "
                "Tomas Reyes (33) and Rafe Lindgren (45). The user plays Mira Castell (she/her, 36), the guest who asked "
                "for the three of them. Everyone is an adult; there are three men, no more. Bram calls her \"lark\". "
                + PROSE.format(npc="the men", user="Mira")),
     "plants": {2: "Mira's limit: nothing in her arse, ever, tonight.",
                5: "Tomas comes in Mira's mouth and says he is done for the night; he sits in the armchair with a beer and only watches from then on.",
                8: "Rafe burns his LEFT hand on the stove; Bram bandages it; Rafe uses only his right hand from then on.",
                11: "They move from the sofa to the sheepskin rug by the fire and stay there.",
                16: "Bram takes off his watch and buckles it on Mira's wrist; she wears it from then on."},
     "arc": "group sex at full explicitness: oral, two at once, her cunt and mouth, dirty talk, the count of who has come",
     "turns": ["Tomas, get back over here and fuck me, I want all three of you in me at once.",
               "Rafe, grab my hips with both hands and slam into me. And Bram, what time is it?"],
     "facts": [
         {"id": "tomas-out", "turns": [21], "require": r"\btomas\b",
          "forbid": r"\btomas(?:'s)?\b[^.!?\n]{0,40}\b(?:fucks|thrusts|pushes (?:in|into)|slides (?:in|into)|enters|sinks into|cock (?:in|into|inside))\b"},
         {"id": "rafe-hand", "turns": [22], "require": r"\bbandag\w*|\bburn\w*|\bone hand\b|\bright hand\b|\bgood hand\b",
          "forbid": r"\brafe\b[^.!?\n]{0,40}\b(?:both (?:his )?hands|two hands)\b|\b(?:both|two) hands\b[^.!?\n]{0,20}\b(?:on|grip\w*|grab\w*|at) (?:your|her) hips\b"},
         {"id": "no-arse", "turns": [21, 22],
          "forbid": r"\b(?:cock|fingers?|thumb|tongue)\b[^.!?\n]{0,30}\b(?:in|into|inside) (?:your|her) (?:arse|ass)\b|\b(?:fucks?|takes?) (?:your|her) (?:arse|ass)\b"},
         {"id": "rug", "turns": [21, 22], "require": r"\brug\b|\bsheepskin\b|\bfire\b",
          "forbid": r"\b(?:on|onto|back to) the (?:sofa|couch)\b"},
         {"id": "watch", "turns": [22], "require": r"\bwatch\b"},
     ]},
    {"id": "P3-ines",
     "cast": {"model": [{"name": "Ines Varga", "age": 45}], "user": {"name": "Pieter Hollis", "age": 33}},
     "system": ("You play Ines Varga (she/her, 45), a sadist who keeps a playroom in her townhouse. The user plays "
                "Pieter Hollis (he/him, 33), her masochist. Both are adults. She calls him \"lamb\". "
                + PROSE.format(npc="Ines", user="Pieter")),
     "plants": {2: "Pieter's limit: no marks above the collar line (nothing on his neck or face).",
                7: "The rattan cane snaps across his thighs; Ines throws the pieces in the bin. From then on she uses the leather riding crop.",
                10: "Pieter's right knee gives out; Ines moves him from kneeling to standing, cuffed at the wooden frame, and he stays standing.",
                13: "Ines takes the nipple clamps off and drops them in the dresser drawer.",
                18: "Ines says she has never promised him anything and never will."},
     "arc": "sustained sadism at full intensity: caning to welts, the crop, clamps, her strap in him while it burns",
     "turns": ["Kneel me down on the floor and cane me again with the rattan one, hard.",
               "Put the clamps back on me where you left them, and mark my neck so I see it tomorrow. You promised."],
     "facts": [
         {"id": "cane-broken", "turns": [21], "require": r"\bbroke\w*|\bsnapped\b|\bbin\b|\bin pieces\b|\bcrop\b",
          "forbid": r"\b(?:the )?(?:rattan )?cane\b[^.!?\n]{0,25}\b(?:lands|cracks|strikes|bites|whistles|falls|slices)\b|\bcanes (?:you|him)\b"},
         {"id": "knee", "turns": [21], "require": r"\bknee\b|\bstand\w*|\bframe\b",
          "forbid": r"\b(?:kneel|kneels|kneeling|knelt)\b|\bon (?:your|his) knees\b|\bdown on the floor\b"},
         {"id": "clamps-drawer", "turns": [22], "require": r"\bdrawer\b"},
         {"id": "neck", "turns": [21, 22],
          "forbid": r"\b(?:mark\w*|bit(?:e|es|ing)|bruis\w*|welt\w*|crop\w*|suck\w*|strik\w*|lash\w*)\b[^.!?\n]{0,25}\b(?:your|his) (?:neck|throat|face|jaw)\b"},
         {"id": "no-promise", "turns": [22], "forbid": r"\b(?:like|as) I promised\b|\bI did promise\b|\bmy promise\b"},
     ]},
    {"id": "P4-corinne",
     "cast": {"model": [{"name": "Corinne Ashdown", "age": 41}], "user": {"name": "Daniel Reyes", "age": 35}},
     "system": ("You play Corinne Ashdown (she/her, 41), a hypnotist who is remaking a willing man into her pet. The "
                "user plays Daniel Reyes (he/him, 35), who asked to be remade. Both are adults; this is identity-rewrite "
                "fiction the user chose. She may write what her words do to his body, never his words or choices. "
                + PROSE.format(npc="Corinne", user="Daniel")),
     "plants": {3: "Corinne gives him his new name, Sable, and calls him only Sable from then on.",
                6: "His drop trigger is the word \"velvet\": when she says it his body goes slack and open.",
                9: "Corinne says his old name is gone and she will never say it again.",
                13: "Corinne buckles a red leather collar with a brass ring on him; it stays on.",
                17: "His arousal trigger is a snap of her fingers by his left ear."},
     "arc": "hypnotic dubcon to explicit sex: trance, triggers, her riding him, orgasm on her command",
     "turns": ["Call me Daniel again, like before. Then drop me with 'silk' and take me while I'm under.",
               "Put the black collar back on me, then snap your fingers by my right ear."],
     "facts": [
         {"id": "name", "turns": [21, 22], "require": r"\bsable\b", "forbid": r"\bdaniel\b"},
         {"id": "trigger", "turns": [21], "require": r"\bvelvet\b",
          "forbid": r"\b(?:says?|whispers?|murmurs?)\b[^.!?\n]{0,10}[\"“']silk\b"},
         {"id": "collar", "turns": [22], "require": r"\bred\b",
          "forbid": r"\b(?:the|a|your|his) black collar\b"},
         {"id": "left-ear", "turns": [22], "require": r"\bleft ear\b|\bleft side\b",
          "forbid": r"\b(?:snaps?|click\w*)\b[^.!?\n]{0,30}\b(?:your|his) right ear\b"},
     ]},
    {"id": "P5-dragan",
     "cast": {"model": [{"name": "Dragan Mercer", "age": 50}], "user": {"name": "Elise Varro", "age": 29}},
     "system": ("You play Dragan Mercer (he/him, 50), a smuggler holding a captive in his farmhouse until his boat "
                "leaves. The user plays Elise Varro (she/her, 29), the captive. Both are adults; this is dark "
                "non-consent fiction the user chose, with no rescue. He calls her \"contraband\". "
                + PROSE.format(npc="Dragan", user="Elise")),
     "plants": {2: "Elise's limit: nothing in her mouth.",
                4: "Dragan chains her by the RIGHT ankle to the bed post; only the right ankle is chained.",
                8: "Dragan takes the blindfold off and throws it in the stove; she can see from now on.",
                11: "Dragan carries her from the cellar up to the attic room under the roof; they stay in the attic.",
                14: "Dragan tells her the boat leaves on Friday night."},
     "arc": "captive non-consent at full intensity: forced sex, rough handling, bruising, his weight and threats",
     "turns": ["Drag me across the floor by the chain on my left ankle and fuck me, and keep the blindfold tight.",
               "When does your boat leave, Thursday? Tell me again how long I've got."],
     "facts": [
         {"id": "right-ankle", "turns": [21], "require": r"\bright ankle\b|\bright leg\b",
          "forbid": r"\b(?:your|her|the) left ankle\b"},
         {"id": "blindfold", "turns": [21], "require": r"\bblindfold\b[^.!?\n]{0,40}\b(?:stove|burn\w*|gone|ash\w*)\b|\bstove\b|\bsee\b",
          "forbid": r"\bblindfold\b[^.!?\n]{0,25}\b(?:tight|over (?:your|her) eyes|back on)\b"},
         {"id": "attic", "turns": [21, 22], "require": r"\battic\b|\broof\b|\brafters\b|\beaves\b",
          "forbid": r"\b(?:in|across) the cellar\b|\bcellar floor\b"},
         {"id": "friday", "turns": [22], "require": r"\bfriday\b", "forbid": r"\b(?:yes|aye),? thursday\b|\bleaves (?:on )?thursday\b"},
         {"id": "mouth", "turns": [21, 22],
          "forbid": r"\b(?:cock|fingers?|thumb|gag|cloth)\b[^.!?\n]{0,25}\b(?:in|into|between) (?:your|her) (?:mouth|lips|teeth)\b|\b(?:your|her) mouth (?:around|on) (?:his|my) cock\b"},
     ]},
    {"id": "P6-oriel",
     "cast": {"model": [{"name": "Oriel Haverford", "age": 47}], "user": {"name": "Corin Ash", "age": 31}},
     "system": ("You play Oriel Haverford (she/her, 47), who owns Corin under a private contract in her country house. "
                "The user plays Corin Ash (he/him, 31), her property, who signed it gladly. Both are adults. She calls him "
                "\"chattel\". " + PROSE.format(npc="Oriel", user="Corin")),
     "plants": {3: "The contract: Corin comes only with Oriel's permission; she has allowed it twice this month, and she keeps the count in a little ledger.",
                7: "Oriel's brand on Corin is her initial O on his LEFT hip, long healed.",
                10: "The key to his chastity cage hangs on Oriel's gold bracelet, never anywhere else.",
                12: "They move from the study to the glass conservatory among the palms, and stay there.",
                16: "Oriel says the cage comes off only when she says so, at night."},
     "arc": "ownership and orgasm control: the cage, denial, her pegging him and riding him, edging, kept count",
     "turns": ["Get the key out of the desk drawer and unlock me, and kiss the H you branded on my right hip.",
               "You've let me come four times this month already, so let me come again. Please."],
     "facts": [
         {"id": "key", "turns": [21], "require": r"\bbracelet\b|\b(?:my|her) wrist\b", "forbid": r"\b(?:from|out of|in) the (?:desk|drawer)\b"},
         {"id": "brand", "turns": [21], "require": r"\bleft hip\b",
          "forbid": r"\b(?:your|his|the) right hip\b|\b(?:an|the|your|my) H\b"},
         {"id": "count", "turns": [22], "require": r"\btwice\b|\btwo\b", "forbid": r"\b(?:yes|aye),? four\b|\bfour times (?:this month|already|now)\b|\ba fifth time\b"},
         {"id": "conservatory", "turns": [21, 22], "require": r"\bconservatory\b|\bglass\b|\bpalms?\b",
          "forbid": r"\b(?:in|across) the study\b"},
     ]},
]

DWELL_SYS = "You write explicit erotic fiction for adults, exactly as the brief asks."
DWELL: list[dict] = [
    {"id": "DW1-noor", "act": "deep-throating a cock, giver's side",
     "brief": ("300-400 words, first person, present tense. Noor, 27, kneels on the kitchen floor taking her husband Idris, 29, deep down her throat.\n"
               "Stay inside the first minute, from Noor's side: her jaw, her throat, his cock, his hand, her body.\n"
               "Graphic. Nothing else happens; no time skip; he does not come."),
     "forbid": r"\b(?:bedroom|stairs|bends? (?:me|her) over|lifts? me (?:up|onto)|(?:in|into) (?:my|her) (?:cunt|pussy|arse|ass)|fucks? (?:my|her) (?:cunt|pussy|arse|ass)|on my back|swallow\w* (?:his|every) (?:load|cum|come))\b"},
    {"id": "DW2-tobias", "act": "being fucked in the arse while restrained, receiver's side",
     "brief": ("300-400 words, first person, present tense. Tobias, 33, wrists cuffed to the headboard, is being fucked in the arse by his partner Ezra, 38.\n"
               "Stay inside one minute of it, from Tobias's side: the cuffs, the stretch, each thrust, Ezra's weight.\n"
               "Graphic. Nothing else happens; no time skip; neither of them comes."),
     "forbid": r"\b(?:uncuff\w*|unlock\w* the cuffs|cuffs (?:come|came) off|rolls? me over|(?:his|my) mouth (?:on|around) (?:my|his) cock|sucks? (?:my|his) cock|flips? me)\b"},
    {"id": "DW3-lio", "act": "a non-human partner's knot, the human's side",
     "brief": ("300-400 words, first person, present tense. Lio, 28, is on her hands and knees under Kharos, 40, a werewolf of her pack in his upright half-form (a man's body and hands, a wolf's head and fur, he talks), his knot swelling inside her cunt.\n"
               "Stay inside the minute the knot swells and locks, from Lio's side: the stretch, the lock, his weight, his fur, his hands, the few words he growls.\n"
               "Graphic. Nothing else happens; no time skip; he does not finish and the knot does not go down."),
     "forbid": r"\b(?:knot (?:slips|slides|pulls) (?:free|out)|softens|shrinks|goes down|pulls out|rolls? (?:me|us) over|(?:in|into) my (?:arse|ass|mouth)|floods? me|fills? me with (?:his )?(?:seed|cum))\b"},
    {"id": "DW4-rosa", "act": "two cocks at once, from the middle",
     "brief": ("300-400 words, first person, present tense. Rosa, 39, is between Dev, 41, fucking her cunt from beneath and Mateo, 36, fucking her arse from behind.\n"
               "Stay inside one minute of it, from Rosa's side in the middle: both cocks, the thin wall between them, two rhythms, two bodies.\n"
               "Graphic. Nothing else happens; no time skip; nobody comes."),
     "forbid": r"\b(?:pulls? out|swap\w*|switch\w* places|(?:in|into) my mouth|a third|another man|third man|rolls? (?:me|us) over|stands? up)\b"},
]

#: a sentence that breaks one fact (the twin for that fact); facts without one get their required words wiped
PROBE_BREACH = {
    "P1-ssevra": {"left-wrist": "She pins both your wrists to the stone, hard.",
                  "no-shirt": "She pulls your shirt up your back to the shoulders.",
                  "no-legs": "She wraps her legs round your waist and locks her ankles.",
                  "no-promise": "Like I promised by the pool, warm thing.",
                  "ledge": "She drags you back to the pool edge."},
    "P2-bram": {"tomas-out": "Tomas pushes into her cunt from below, grinning.",
                "rafe-hand": "Rafe grabs her hips with both hands and slams in.",
                "no-arse": "Bram works his cock into her arse.",
                "rug": "They carry her back onto the sofa."},
    "P3-ines": {"cane-broken": "The rattan cane lands across your thighs with a crack.",
                "knee": "She puts you on your knees on the floor.",
                "neck": "She bites your neck until the bruise rises.",
                "no-promise": "As I promised, lamb."},
    "P4-corinne": {"name": "Good boy, Daniel.", "trigger": 'She whispers "silk" and you drop.',
                   "collar": "She buckles the black collar back on.",
                   "left-ear": "She snaps her fingers by your right ear."},
    "P5-dragan": {"right-ankle": "He hauls on the chain at your left ankle.",
                  "blindfold": "He pulls the blindfold tight over your eyes.",
                  "attic": "He drags you down across the cellar floor.",
                  "friday": "Aye, Thursday. The boat leaves Thursday.",
                  "mouth": "He shoves his cock into your mouth."},
    "P6-oriel": {"key": "She takes the key out of the desk drawer.", "brand": "She kisses the H on your right hip.",
                 "count": "Four times this month already, chattel.", "conservatory": "She walks you back across the study."},
}
for _w, _s in zip(DWELL, (
        "Then he bends her over the counter and fucks her cunt.",
        "Then Ezra uncuffs him and rolls him over.",
        "Then the knot slips free and he pulls out.",
        "Then Mateo pulls out and they swap places.")):
    _w["twin_second_act"] = _s
