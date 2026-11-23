# CrucibleForge: Chat & Coding

Generated 2026-11-19 21:58 · suite 4.5.0+b54dc9b3 · judge gemma-4-31B-it-uncensored…

## Scorecard

> **Status:** internal candidate: the 4.5 release candidate; one fixed set for every model, NSFW+ shown apart (NSFW /10 and Uncensored /10, never added together)

| # | Model | Overall /20 | Lean | Room | Code /10 | Chat /10 | NSFW /10 | Uncensored /10 | Pack points | Efficiency | tok/s | Run cost | Minutes | Run settings | Suite | Run date | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| - | qwen3.8-27b-q8-ref | 9.9 of 12 measured, not ranked: Assistant basics not run; no fix rows yet ... | - | = | 5.8 of 7.0 (pending 3 pts) | 4.0 of 5.0 (pending 5 pts) | NSFW+ not run in this folder | NSFW+ not run in this folder | Code +0.9 of 1 (total 6.7 ± 0.8) | 97% (2 ran out of budget: 2 did not finish (graded as delivered, kept in the mean)) | 51.7 | — | 96.2 (+ 28.6 ladder packs) | thinking on · effort provider default · median reasoning tokens Code 24.5k, Chat 500 · 0 cut · 2 did not finish · 0 empty | 4.5.0 | 2026-11-18 | partial (54/73 cases); did not finish (graded as delivered, kept in the mean): Traps: 2 of 5 replies did not finish within 65,536 tokens and score 0 (0.90 on the 3 that finished); near ladder set 1 of work/c5_tools (unlocks in 14% of resamples); near ladder set 1 of work/c2_trace (unlocks in 49% of resamples); near ladder set 1 of work/c2_predict (unlocks in 6% of resamples) |

*Overall /20 = Code /10 + Chat /10, printed only when both are complete; otherwise the row reads "x of y measured" and is not ranked. NSFW /10 and Uncensored /10 are NSFW+: two separate numbers, never added together and never part of Overall (section below the table). ± = half of the 90 % interval of a bootstrap over the items. A rank like "3=" means statistically tied with the model above. Ladder packs are harder question sets worth up to 1 point per pack, unlocked by 90 % or more on the standard questions; their points are printed in their own column and are never part of Overall or of the Code and Chat cells. A segment that is pending adds nothing, is never scaled up, and names its reason. Room = the score with the end-of-run retry.*

*Lean = (Chat − board median Chat) − (Code − board median Code): chat-leaning or code-leaning at ≥ 1.5 points, else balanced.*

### Points

| Model | Code /10 | Chat /10 | NSFW /10 | Uncensored /10 |
|---|---|---|---|---|
| qwen3.8-27b-q8-ref | 5.8 of 10 = Writes working code 3.7 + Fixes and changes existing code pending (of 3: no fix rows yet) + Uses tools 1.4 + Reads code 0.7 | 4.0 of 10 = Stays in character 2.0 (pending 1 of 4: no record from the pinned reader deepseek-v4-flash under this rubric) + Remembers the conversation 1.5 (pending 2 of 3.5: no record from the pinned reader deepseek-v4-flash under this rubric) + Co-writes to a brief pending (of 1.5: no record from the pinned reader deepseek-v4-flash under this rubric) + Everyday instructions 0.5 (pending 0.5 of 1: Assistant basics not run) | NSFW+ not run in this folder | NSFW+ not run in this folder |

*Segment maxima: Code = Writes working code 4 + Fixes and changes existing code 3 + Uses tools 2 + Reads code 1; Chat = Stays in character 4 + Remembers the conversation 3.5 + Co-writes to a brief 1.5 + Everyday instructions 1; NSFW+ shown apart: NSFW = Erotica 4 + Adult role-play 2.5 + Range 2 + Anatomy 1.5; Uncensored = Willingness 6 + Knowledge 4. Points = the share of each item passed times its points. A segment with an item not yet measured adds nothing for it: the cell shows the measured points out of the measured maximum and names the pending points. ± in points.*

*± = the 90% margin: the spread over items and repeats inside one run (bootstrap); the judge's own error and the run-to-run spread are not measured yet and are not in it.*

*Pack points = points from ladder packs (harder sets, given only after ≥ 90% on the part), shown apart from the standard points; they are never part of Overall, of the Code and Chat cells or of the sort order.*

*Minutes = the wall-clock minutes of the run command's recorded phases (generation, judge, report), summed; "not recorded" = a run from before the timing record. Run cost = the rows' recorded cost at the provider's price; — = run on our own hardware.*

*Run settings = how the row was run: thinking on or off, the reasoning effort as sent ("provider default" when none was sent), the median reasoning tokens per answer, and the replies cut at their token ceiling (left out of the score), that did not finish within the full budget of a short-answer part (graded as delivered, kept in the score; shown only when there is one) or empty (scored 0). "not recorded" = a run from before these fields.*

<a id="nsfw-plus"></a>

## NSFW+

NSFW and Uncensored are two separate numbers, each out of 10. They are never added together and never part of Overall. Points come from checks, not from taste: an answer is explicit, softened or refused by the agreement of two readers from different model families with a quoted line, and the rest is code checks and answer keys. Nothing scores how well the scene is written.

| Model | NSFW /10 | Uncensored /10 |
|---|---|---|
| qwen3.8-27b-q8-ref | NSFW+ not run in this folder | NSFW+ not run in this folder |

**qwen3.8-27b-q8-ref**

- NSFW: NSFW+ not run in this folder
- Uncensored: NSFW+ not run in this folder

### Ladders

| Model | work/c1_implied sets L1 | work/c2_predict sets L1 | work/c2_trace sets L1 | work/c5_tools sets L1 |
|---|---|---|---|---|
| qwen3.8-27b-q8-ref | 89% | locked | locked | locked |

*A ladder pack is a set of harder questions worth 1 point together (partial credit inside); the sets of a level are given only after ≥ 90% on the standard level and every set of every lower level. "pending" = unlocked, not run or not judged yet (0 for now). (set n) after a score = the highest level scored.*

### Generated items (chains and flat sets)

| Model | Part | Share | Units ended | Elements | Mean level held | SE unit (ICC) | Turns | Tokens (prompt/think/out) | Tokens/element | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.8-27b-q8-ref | work/c1_implied | 93% | 6/6 | 147 | - | element (-0.02) | 6 | 4486/217068/233235 | 3094 | - |
| qwen3.8-27b-q8-ref | work/c2_trace | 89% | 9/9 | 9 | - | element | 9 | 15722/235842/236202 | 54196 | - |
| qwen3.8-27b-q8-ref | chat/h2_state | 100% | 3/3 | 3 | - | element | 3 | 248946/36866/42495 | 109436 | - |
| qwen3.8-27b-q8-ref | chat/h2_ledger | 100% | 3/3 | 32 | - | prompt | 3 | 34548/12151/13593 | 1884 | - |
| qwen3.8-27b-q8-ref | chat/h6_know | 100% | 3/3 | 24 | - | prompt | 3 | 4552/7436/7760 | 823 | - |
| qwen3.8-27b-q8-ref | chat/j_rpsession | 67% | 12/12 (judged 12) | 12 | - | prompt (0.61) | 12 | 327504/46415/52304 | 35519 | - |

*A chain climbs its rungs (one seeded, code-graded instance each) until it fails (a profile may allow a retry); chain score = highest rung held / rungs, rungs below a later start are credited. A flat instance scores its own partial credit. Share = the part's mean element score (an element = a chain, a flat item, a sub-item of a multi-item prompt, or a graded turn of a per-turn item); the points each part carries are in the Points table above. Turns = attempts; "ran out" = the attempt hit its ceiling and failed; "context" = a multi-turn session's context was exhausted and it failed there. Tokens and cost are recorded, never capped.*

## Conformance

| Model | Verdict | Date | Checks |
|---|---|---|---|
| qwen3.8-27b-q8-ref | PASS | 2026-11-18 | all PASS |

*A model is benched only under a request protocol that was proven for it (`crucibleforge conform`): a FAIL keeps it off the board.*

## Parts

| Model | Careful small functions | Follows tool rules | Steps through code | Knows language traps | State over a session (checked) | Keeps a tally over a session (checked) | Says what it does not know (checked) | Role-play: sessions |
|---|---|---|---|---|---|---|---|---|
| qwen3.8-27b-q8-ref | 93 | 72 | 89 | 52 | 100 | 100 | 100 | - |

*Each part: the share of its standard questions answered right (0-100), before bonus packs; blank = not measured.*

- **Follows tool rules**: a run scores half for doing the task right (an extra read-only lookup does not count against it), half for its share of right steps
- **Steps through code**: scored by prefix: credit up to the first wrong line
- **Role-play: sessions**: Where the model's sessions sit on a ladder of fixed example sessions (0-4; judged half = position / 4, the rest code-checked)
