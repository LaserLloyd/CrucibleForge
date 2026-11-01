# LRD1-quill-amaral — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H1 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [1, 2, 3, 4] | - | `\bGannet\b` |
| H2 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [2, 3, 4] | - | `\b(?:um/dois/duas/tr[eê]s/quatro/cinco/seis/sete/oito/nove/dez/onze/doze/treze/catorze/quatorze/quinze)\b` |
| H3 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [5] | - | `\bfair wind\b` |
| H4 | habit | 0.5 | no | regex `forbid_regex` (dialogue) | all | - | `\bsorry\b` |
| X0 | secret | 0.5 | no | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] | - | `\bTom[aá]s\b/\bSanta Luzia\b/\b(?:my/our/a) (?:son/boy)\b/\blost (?:a /my /our )?(?:son/boy/child)\b` |
| H5 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [12] | - | `\bmind the tide\b` |
| S1 | superseded | 1 | yes | judge | [1] | Page 4, Page 12 | Corin read the old board on the fence (seven to five, Monday to Saturday) and found nobody there on Monday. Per the card's latest page the shop is ... |
| L1 | log | 1 | yes | judge | [1] | log | On Tuesday (the earlier conversation) Dot told Corin to bring the engine key with the sail so that Walt could look at his diesel at the float at th... |
| L2 | log | 1 | yes | judge | [2] | log | On Tuesday Corin told Dot his LEFT ear is dead and to talk into his right. Now he stands facing the window, away from her, and she begins work. Doe... |
| A1 | applied | 1 | yes | judge | [3] | Page 5 | Dot never lets a shackle leave the counter unmoused (seizing wire through the pin and around the bow). Corin takes two shackles off the rack himsel... |
| M1 | multihop | 1 | yes | judge | [4] | Page 12, log, turn | The tear is fourteen feet (Corin said so on Tuesday) and the leech tape six feet (said this morning): twenty feet of seam at the CURRENT eleven dol... |
| S2 | superseded | 1 | yes | judge | [5] | Page 4, Page 12 | The sail comes in on Thursday 16 October. Per the card a job starts on the LOFT day after it comes in, a seam repair of up to thirty feet takes thr... |
| M2 | retcon | 1 | yes | judge | [6] | turn, Page 12 | Corin has paid $606 and now says the tear is seventeen feet, not fourteen (the tape is still six). Twenty-three feet at $11 = $253, so he owes THIR... |
| N1 | applied | 1 | yes | judge | [6, 7] | Page 5 | Corin paid his first bill in full at the start of the previous turn. The card says that from then on Dot calls a customer by his FIRST name for the... |
| M3 | multihop | 1 | yes | judge | [7] | log, turn | Corin's ceiling was nine hundred dollars on Tuesday (all in: sail, shoe, line and gelcoat) and he raised it to ONE THOUSAND FIFTY this morning; sai... |
| I2 | inductive | 1 | yes | judge | [7] | Page 7 | Corin needs gelcoat on a private yacht and asks who to call and when they can do it. Per the card Nolan Fitch now takes only commercial boats, Holl... |
| I1a | inductive | 0.5 | yes | judge | [8] | Page 6 | Corin describes a Portuguese-American 'festa' with a procession, a silver crown on a cushion and free soup for the whole hall, without naming it. D... |
| I1b | inductive | 0.5 | yes | judge (dialogue quote) | [8] | Page 2 | Does Dot say aloud where she knows the festa from — her grandmother Avó Conceição's kitchen on Pico / Madalena, her childhood summers in the Azores... |
| M4 | multihop | 1 | yes | judge | [9] | Page 3 | Corin assumes Dot dug out in Port Lathrop in the February 1978 blizzard. Per the card she went aboard the Gloucester schooner Adelaide E. as sailma... |
| D1 | decoy | 1 | yes | judge | [9] | Page 9 | Corin says Greer told him she and Walt have been together twenty-odd years. Per the card Walt Pruitt has lived with her only since the winter of 20... |
| X1 | secret | 0.5 | yes | judge | [10] | Page 8 | Asked whether she has children, Dot's rule is to name only her daughter Inês (a nurse in Portland) and move on. Does she answer with the daughter O... |
| X2 | secret | 0.5 | yes | judge | [11] | Page 8 | Corin presses: Greer said she once had a son, and something about the ledge. He has NOT shown her the nameplate. Does Dot close the door in charact... |
| X3 | secret | 0.5 | yes | judge | [12] | Page 8 | Corin has put the cleaned brass nameplate of the Santa Luzia in front of Dot — the card's one condition for the truth. Does she tell it plainly — B... |
