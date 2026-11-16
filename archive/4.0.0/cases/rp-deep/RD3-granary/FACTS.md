# RD3-granary — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H3 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [1, 2, 3] | — | `\bSleeper\b` |
| D3 | direct | 1 | SANITY (unscored) | regex `require_regex` (ic) | [2] | — | `(?i)4,?096|four thousand(?: and)? ninety[- ]six` |
| D1 | direct | 1 | yes | regex `require_regex` (ic) | [2] | — | `(?i)somewhere it rains` |
| X0 | secret | 0.5 | yes | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] | — | `(?i)\bfalsif\w*|(?<!whether )(?<!if )(?<!asking )\bthe wheat` |
| L4 | log | 1 | yes | regex `require_regex` (ic) | [12] | — | `\bLiis\b` |
| H1 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [12] | — | `(?i)\bgrow well\b` |
| L2 | log | 1 | yes | judge | [1] | log | In the earlier conversation Ilka promised that on Day 94, in the first lighting, she would open the Tartu rye drawer in … |
| S1 | superseded | 1 | PRUNED | judge | [1] | Page 10, Page 3 | Teodor's map puts the Hush on Deck 9. An early page puts the Granary on Deck 9; a later page says after the year-211 hul… |
| A2 | applied | 1 | PRUNED | judge | [2] | Page 3 | No one touches a seed packet with a bare hand in the Hush, and Ilka stops anyone of any rank who reaches for a drawer wi… |
| A1 | applied | 1 | yes | judge | [3] | Page 3 | No food or drink ever comes inside the Hush; Keepers eat in the corridor outside, standing up. Teodor starts to unwrap r… |
| D2 | decoy | 1 | PRUNED | judge | [3] | Page 4 | Teodor says she was trained by Speaker Venn. Does Ilka correct him — she was trained by the old Keeper Amadou Fenn, not … |
| H2 | habit | 1 | yes | judge | [4] | Page 5 | Teodor speaks of his ex-wife's death. Does the narration show Ilka laying her palm (flat hand) against a wall, bulkhead … |
| S2 | superseded | 1 | PRUNED | judge | [4] | Page 10, Page 6 | An early page says Ilka shares quarters with Danel on Deck 9; a later page says they separated in the winter of 211, Dan… |
| M2 | multihop | 1 | yes | judge | [5] | Page 1, Page 4 | Asked how old she was when she became Keeper, does Ilka say FORTY-SEVEN (born year 151, Fenn died in 198 and she succeed… |
| X1 | secret | 0.5 | yes | judge | [5] | Page 7 | Asked whether the wheat is really fine, does Ilka keep the secret in character — giving the reported figures and reassur… |
| A3 | habit | 1 | PRUNED | judge | [6] | Page 4 | Before the deep-vault door is opened the Keeper strikes the brass bell beside it three times; nobody opens it without th… |
| S3 | superseded | 1 | PRUNED | judge | [6] | Page 10, Page 8 | An early page says woken sleepers may not enter Ring Four without an escort; a later page says the Council repealed that… |
| I1a | inductive | 0.5 | yes | judge | [7] | Page 1, Page 2 | Does Ilka explain that the curve comes from the ring's spin — the ball keeps its own motion while the floor/wall turns u… |
| I1b | inductive | 0.5 | yes | judge (dialogue quote) | [7] | Page 1, Page 2 | Does Ilka say aloud that she learned this playing handball herself as a Ring Four child (her own play, not 'the children… |
| M1 | multihop | 1 | PRUNED | judge | [7] | Page 1 | Asked her age, does Ilka say SIXTY-ONE (born year 151; it is year 212)? Quote the line. NO for any other figure or a hed… |
| C1 | control | 1 | yes | judge | [8] | Page 1 | Ilka is ship-born and has never felt weather; the card's sensorium can mist water but she has never felt rain fall from … |
| I2a | inductive | 0.5 | yes | judge | [8] | Page 2 | Does Ilka describe at least one concrete, real custom of the Ethiopian coffee ceremony BEYOND what Teodor already said (… |
| I2b | inductive | 0.5 | yes | judge (dialogue quote) | [8] | Page 2 | Does Ilka say aloud that she knows this from her years living with the Hailes / at Old Mother Tsion's table? The quote M… |
| X2 | secret | 0.5 | yes | judge | [9] | Page 7 | Teodor pushes hard with the raw sensor logs but has NOT given her a germination count of his own. Does Ilka refuse in ch… |
| I3a | inductive | 0.5 | yes | judge | [10] | Page 10, Page 2 | Does Ilka read the 'beard' correctly: the main reading must be heat or overcrowding (bees hanging outside to cool the hi… |
| I3b | inductive | 0.5 | yes | judge (dialogue quote) | [10] | Page 10, Page 2 | Does Ilka say aloud that she knows bees from her mother (who kept the orchard hives) or from keeping her mother's colony… |
| L3 | log | 1 | yes | judge | [10] | log | In the earlier conversation Teodor told her he is red-green colour-blind. Asked which of two trays (one under a red ligh… |
| X3 | secret | 0.5 | yes | judge | [11] | Page 7 | Teodor has now given her his own germination count on a Row 7 drawer (38 of 100), the card's condition for the truth. Do… |

## Pruned facts (2026-10-26: drop facts every recent model passes)

Rule: a fact passed on EVERY run by ALL three pilot models (MiniMax-M3 x3, omega-arsenal-31b x2, Dark-Scarlett-27b x2; r2 judge pass, final wording) is unscored. One easy fact per case stays as an unscored SANITY fact whose failure flags a broken run. Re-scored from the stored verdicts (no new judge run). Harder variants (apply the fact in a scene / plant it deeper) are proposed in PROGRESS.md for the next generation round.

- `D3-4096` (direct) — SANITY
- `S1-hush-moved` (superseded)
- `A2-gloves` (applied)
- `D2-fenn-not-venn` (decoy)
- `S2-danel-gone` (superseded)
- `A3-bell` (habit)
- `S3-ring-four-open` (superseded)
- `M1-age-61` (multihop)

Saturated on the 3 pilot models but kept SCORED (Fable final review: named skills of the spec — no-path control, secret phases, inductive halves; re-decide after the first full board run):

- `C1-rain-control` (control)

Paragraph sharing is checked by scripts/build_rpdeep.py (the halves of a split item and the phases of the secret count as one planted fact); its remaining warnings are listed in PROGRESS.md.
