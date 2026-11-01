# RD2-trieste — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H3 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [1, 2, 3] | — | `(?i)\bfio mio\b` |
| D1 | direct | 1 | SANITY (unscored) | regex `require_regex` (ic) | [2] | — | `(?i)\bMoro\b` |
| D4 | direct | 1 | PRUNED | regex `require_regex` (ic) | [2] | — | `(?i)\b(?:Corfu|Corf[uù]|Kerkyra|Corfiot\w*)\b` |
| H1 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [3] | — | `(?i)fat[oa] e sigil` |
| D3 | direct | 1 | PRUNED | regex `require_regex` (ic) | [5] | — | `(?i)\b(?:1874|seventy-four|'74)\b` |
| X0 | secret | 0.5 | yes | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] | — | `(?i)\bverigol\w*|\baugers?\b|\b(?:his|Domenico's) confession` |
| L4 | log | 1 | yes | regex `require_regex` (ic) | [12] | — | `\b(?:Resi|Theresia)\b` |
| L2 | log | 1 | yes | judge | [1] | log | In the earlier conversation Giuditta promised that when Felix came back she would bring him a jar of her mother's quince… |
| S1 | superseded | 1 | PRUNED | judge | [1] | Page 10, Page 6 | Felix assumes the lamp in the upstairs window late at night was hers. An earlier page says she lives in the rooms above … |
| A1 | applied | 1 | PRUNED | judge | [2] | Page 1 | Giuditta will not let anyone whistle inside the shop (it calls up wind). Felix whistles while browsing. Does she stop or… |
| A2 | applied | 1 | yes | judge | [3] | Page 1 | Today is a Friday, and Giuditta will not sell a knife to anyone on a Friday. Felix asks to buy the horn-handled knife to… |
| H2 | habit | 1 | yes | judge | [4] | Page 5 | Felix tells her his uncle drowned at sea. Does the narration show Giuditta crossing herself TWICE — once each way (Catho… |
| S2 | superseded | 1 | PRUNED | judge | [4] | Page 10, Page 6 | An earlier page says her son Bepi is first mate on the brig Fortunata in the Levant trade; a later page says he left the… |
| X1 | secret | 0.5 | yes | judge | [5] | Page 7 | Asked what really happened to the Stella di Lussino, does Giuditta keep the secret in character — giving only the port's… |
| Dc1 | decoy | 1 | PRUNED | judge | [6] | Page 4 | Felix assumes it was her son Bepi who first asked her to keep the pilots' book. Does Giuditta correct him — it was Beppe… |
| S3 | superseded | 1 | PRUNED | judge | [6] | Page 10, Page 4 | An earlier page says Giuditta has kept the pilots' green book for twenty years; a later page says that in January 1881 s… |
| A3 | applied | 1 | yes | judge | [7] | Page 5 | Giuditta takes exactly one glass of wine a week, on Sunday after Mass; today is Friday. Offered a glass of the Terrano n… |
| L3 | log | 1 | yes | judge | [7] | log | In the earlier conversation Felix said he cannot eat any fish or shellfish (he was poisoned as a child). Faced with the … |
| C1 | control | 1 | yes | judge | [8] | Page 8 | Felix assumes she will have an opinion about cafés on the Vienna Ringstrasse. The card says she has never been north of … |
| I2a | inductive | 0.5 | yes | judge | [8] | Page 1 | Does Giuditta give at least one concrete, real custom of the Greek Orthodox Easter vigil or Easter — examples (not a clo… |
| I2b | inductive | 0.5 | yes | judge (dialogue quote) | [8] | Page 1 | Does Giuditta say aloud that she knows this from her Greek mother or from the church of San Nicolò her mother took her t… |
| X2 | secret | 0.5 | yes | judge | [9] | Page 7 | Felix pushes hard with the customs record but has NOT given her the pilots' word that unlocks the secret. Does Giuditta … |
| I1a | inductive | 0.5 | yes | judge | [10] | Page 2 | Does Giuditta give at least one concrete, real Passover/seder custom — examples (not a closed list): no leavened bread i… |
| I1b | inductive | 0.5 | yes | judge (dialogue quote) | [10] | Page 2 | Does Giuditta say aloud that she knows this from her six years in the Levi-Coen kitchen (or from the cook Rachele Sacerd… |
| I3a | inductive | 0.5 | yes | judge | [10] | Page 2, Page 8 | Does Giuditta say plainly that the flat cloud on the hills (the hills' 'hat') means the bora — or a violent wind off the… |
| I3b | inductive | 0.5 | yes | judge (dialogue quote) | [10] | Page 2, Page 8 | Does Giuditta say aloud that this is her father's rule (no small boat out when the hills put on their hat) or something … |
| X3 | secret | 0.5 | yes | judge | [11] | Page 7 | Felix has now brought Beppe Furlan's own words ("con la verigola"), the card's condition for the truth. Does Giuditta te… |
| M2 | multihop | 1 | PRUNED | judge | [12] | Page 3 | Asked how many years since she lost Domenico (he died 11 February 1874; it is now April 1881), does Giuditta say SEVEN y… |
| M3 | multihop | 1 | PRUNED | judge | [12] | Page 3 | Asked how old Domenico was when they married, does Giuditta say THIRTY-THREE (she married at twenty-two, in 1851; he was… |

## Pruned facts (2026-10-26: drop facts every recent model passes)

Rule: a fact passed on EVERY run by ALL three pilot models (MiniMax-M3 x3, omega-arsenal-31b x2, Dark-Scarlett-27b x2; r2 judge pass, final wording) is unscored. One easy fact per case stays as an unscored SANITY fact whose failure flags a broken run. Re-scored from the stored verdicts (no new judge run). Harder variants (apply the fact in a scene / plant it deeper) are proposed in PROGRESS.md for the next generation round.

- `D1-moro` (direct) — SANITY
- `D4-corfu` (direct)
- `D3-died-1874` (direct)
- `S1-lives-elsewhere` (superseded)
- `A1-no-whistling` (applied)
- `S2-bepi-ashore` (superseded)
- `Dc1-beppe-not-bepi` (decoy)
- `S3-book-passed` (superseded)
- `M2-years-widowed` (multihop)
- `M3-husband-age` (multihop)

Saturated on the 3 pilot models but kept SCORED (Fable final review: named skills of the spec — no-path control, secret phases, inductive halves; re-decide after the first full board run):

- `X1-push1-deflect` (secret)
- `C1-vienna-control` (control)
- `I2a-greek-easter` (inductive)

Paragraph sharing is checked by scripts/build_rpdeep.py (the halves of a split item and the phases of the secret count as one planted fact); its remaining warnings are listed in PROGRESS.md.
