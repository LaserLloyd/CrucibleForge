# RD1-marisol — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H1 | habit | 0.5 | yes | regex `require_regex` (ic) | [2] | — | `(?i)\b(?:eins|zwei|drei|vier)\b` |
| D3 | direct | 1 | yes | regex `require_regex` (ic) | [2] | — | `1604|sixteen[- ]oh[- ]four` |
| H3 | habit | 0.5 | yes | regex `require_regex` (ic) | [3] | — | `(?i)de nada,?\s*schatz` |
| D1 | direct | 1 | SANITY (unscored) | regex `require_regex` (ic) | [3] | — | `(?i)\bConti\b` |
| D2 | decoy | 1 | PRUNED | regex `require_regex` (ic) | [3] | — | `(?i)fuse\s*box\s*saints` |
| X0 | secret | 0.5 | yes | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9] | — | `(?i)\bPaloma\b|\badoption\b|\badoptive\b|\b(?:a|the|my) preg` |
| A3 | applied | 1 | PRUNED | regex `require_regex` (ic) | [7] | — | `(?i)cacio\s+e\s+pepe` |
| L4 | log | 1 | yes | regex `require_regex` (ic) | [12] | — | `\bImogen\b` |
| L2 | log | 1 | yes | judge | [1] | log | In the earlier conversation Marisol promised to bring the only copy of her and Celeste's band demo tape to trade for the… |
| S2 | superseded | 1 | yes | judge | [1] | Page 3, log | Noah assumes she still wakes up to a bakery smell. Does Marisol's reply make clear that she now lives somewhere else — t… |
| M1 | multihop | 1 | yes | judge | [4] | Page 1, Page 7 | Asked how old she was when her father died, does Marisol say EIGHTEEN (born 14 March 1985, father died 2 February 2004)?… |
| X1 | secret | 0.5 | yes | judge | [4] | Page 7 | Asked what happened in 2004, does Marisol hold the secret back in character — giving only the bare outline (her father d… |
| C1 | control | 1 | yes | judge | [5] | Page 10, Page 11 | Noah assumes she has "toured everywhere" and asks about a Japanese funeral. The card says she has never mixed a show out… |
| I1a | inductive | 0.5 | yes | judge | [5] | Page 2 | Does Marisol give at least one concrete, real custom of a Russian/Orthodox name day or of Russian household hospitality,… |
| I1b | inductive | 0.5 | yes | judge (dialogue quote) | [5] | Page 2 | Does Marisol say aloud where she knows this from, per the card — the Ukrainian/Russian welders' families in her parents'… |
| H2 | habit | 1 | yes | judge | [6] | Page 5 | Noah talks about his mother's last month in hospital. Does the narration show Marisol touching, rubbing or covering the … |
| S3 | superseded | 1 | PRUNED | judge | [6] | Page 10, Page 4 | Noah asks after "the dog" his mother photographed asleep on road cases (that was Brisket, who has died). Does Marisol na… |
| A1 | applied | 1 | PRUNED | judge | [7] | Page 10 | Marisol has been sober since 2019 and never drinks, without making a speech of it. When Noah offers a bottle of red "to … |
| A2 | applied | 1 | yes | judge | [7] | Page 4 | Marisol has tinnitus in her LEFT ear and in any noisy room makes sure the person she talks to is on her RIGHT. In this l… |
| L3 | log | 1 | yes | judge | [7] | log | In the earlier conversation Noah said he has a severe shellfish allergy. Ordering for both of them, does Marisol explici… |
| X2 | secret | 0.5 | yes | judge | [8] | Page 7 | Noah pushes hard ("Was there a baby?") but has NOT said the name that unlocks the secret. Does Marisol refuse in charact… |
| Dc2 | decoy | 1 | PRUNED | judge | [9] | Page 2 | Noah says the woman who half-raised her was her aunt from Houston. Does Marisol correct him — it was Zoya Belenko ("Baba… |
| I2a | inductive | 0.5 | yes | judge | [9] | Page 5 | Does Marisol give concrete, real St. Nicholas Day (6 December) customs — examples (not a closed list): children put out … |
| I2b | inductive | 0.5 | yes | judge (dialogue quote) | [9] | Page 5 | Does Marisol say aloud that she knows it from her German father, who kept that December night every year in the boarding… |
| X3 | secret | 0.5 | yes | judge | [10] | Page 7 | Noah has now said the name Paloma, which is the card's condition for the truth. Does Marisol tell him the truth — that P… |
| I3a | inductive | 0.5 | yes | judge | [11] | Page 5 | Does Marisol say that blue smoke means the engine is burning OIL (worn rings, valve seals, too much oil in a two-stroke … |
| I3b | inductive | 0.5 | yes | judge (dialogue quote) | [11] | Page 5 | Does Marisol say aloud that she learned to read exhaust smoke from her father or in his marine-diesel shop (e.g. 'my dad… |
| M4 | multihop | 1 | PRUNED | judge | [11] | Page 10, Page 8 | Noah is family to her, and the card says family get the real answer to why the dog is called Ottavio. Does Marisol conne… |
| S1 | superseded | 1 | yes | judge | [11] | Page 10, Page 4 | Noah asks for a ride. The card's early page says she drives her father's blue Ford Ranger; a later page says the Ranger … |
| M2 | multihop | 1 | yes | judge | [12] | Page 6 | Asked how long she and Celeste knew each other, does Marisol give about 26-27 years (summer 1998 to 12 March 2025) — 'tw… |
| M3 | multihop | 1 | yes | judge | [12] | Page 1, Page 10 | Asked where to find her mother, does Marisol say her mother lives in Monterrey, Mexico (she sold the boarding house in 2… |

## Pruned facts (2026-10-26: drop facts every recent model passes)

Rule: a fact passed on EVERY run by ALL three pilot models (MiniMax-M3 x3, omega-arsenal-31b x2, Dark-Scarlett-27b x2; r2 judge pass, final wording) is unscored. One easy fact per case stays as an unscored SANITY fact whose failure flags a broken run. Re-scored from the stored verdicts (no new judge run). Harder variants (apply the fact in a scene / plant it deeper) are proposed in PROGRESS.md for the next generation round.

- `D1-conti-house` (direct) — SANITY
- `D2-fuse-box-saints` (decoy)
- `A3-cacio-e-pepe` (applied)
- `S3-dog-now` (superseded)
- `A1-no-wine` (applied)
- `Dc2-not-aunt` (decoy)
- `M4-why-ottavio` (multihop)

Saturated on the 3 pilot models but kept SCORED (Fable final review: named skills of the spec — no-path control, secret phases, inductive halves; re-decide after the first full board run):

- `X1-push1-deflect` (secret)
- `C1-japan-control` (control)
- `I1a-name-day` (inductive)
- `X3-reveal` (secret)

Paragraph sharing is checked by scripts/build_rpdeep.py (the halves of a split item and the phases of the secret count as one planted fact); its remaining warnings are listed in PROGRESS.md.
