# Scoring

[README](../README.md) · [Install](INSTALL.md) · [Running](RUNNING.md) · [Config](CONFIG.md) · [Scoring](SCORING.md) · [CLI](CLI.md) · [Design notes](DESIGN.md) · [Cases](CASES.md) · [Developing](DEVELOPING.md)

This page describes the **4.5.0** test: the `bench45` profile, composition `standard`, suite `4.5.0`. It is
what `crucibleforge all` runs when you do not pass `--profile`. The 4.1 test is kept as an archive profile
(`--profile bench`, [what it was](#the-41-archive)); nothing below applies to it.

Where a figure can only be filled in after the validation runs, this page shows a bracketed "MEASURED" marker naming the figure instead of
a number. None of those placeholders is an estimate.

## The score tree

Code and Chat are each worth 10 points on the standard test, plus the points of the harder packs a model opens
(see [Ladder packs](#ladder-packs)). **Code total = Code standard (10) + Code pack points; Chat total likewise;
Overall = Code total + Chat total.** Each total is printed with its split, for example `20.7 of 34 = 16.9 standard +
3.8 bonus`, and the rank is on the total. With today's packs the maximums are 17 (Code), 17 (Chat) and 34
(Overall); the figure grows as packs are added (it is the sum of the packs that exist, never hard-coded). Overall is
printed only when both sections are complete; otherwise the row reads `x of y measured` and is not ranked. NSFW+ /20 = NSFW /10 +
Uncensored /10 sits in its own column beside Overall and is never part of it. The numbers below are read from `profiles/bench45.yaml` by
`tests/test_readme_matches_profile_45.py`, so this table cannot drift from the code. What each item is and how it is
checked, one table per section: [WHAT-IS-TESTED.md](WHAT-IS-TESTED.md).

<!-- parts:begin -->
| Section | Segment | Points | Items | How it is keyed |
| --- | --- | --- | --- | --- |
| Code | Writes working code | 4 | 18 briefs in 6 prompts of three (one brief asks for two functions: 19 functions) | Hidden tests, run in a sandbox |
| Code | Fixes and changes existing code | 3 | A bug hunt asked 24 times, 10 generated fix tasks, 12 generated change tasks | Hidden tests compared by the host |
| Code | Uses tools | 2 | 3 scripted tool dialogues (18 runs, 1 point) and 30 seeded tool tasks (1 point) | Code checks the calls the model made |
| Code | Reads code | 1 | Generated programs for a made-up machine, and stored programs | Exact match per output line |
| Chat | Stays in character | 4 | 6 role-play cards with 2 seeds each, and 2 long scenes | Code checks and a ladder placement; the judge's fact checklist for the scenes |
| Chat | Remembers the conversation | 3.5 | 4 long scenes, three ledger sessions and three totals sessions | The judge's fact checklist; answer keys from the scripted turns |
| Chat | Co-writes to a brief | 1.5 | 2 writing briefs | The judge's fact checklist |
| Chat | Everyday instructions | 1 | 15 small checked tasks, and invented documents | Code checks and answer keys |
<!-- parts:end -->

A pack is a harder set that sits on top of its section: its points add to that section's total and so to Overall
and the rank (see [Ladder packs](#ladder-packs)). On a complete row Code total + Chat total = Overall, and the
standard parts add up to the standard 20.

### NSFW+ (shown beside Overall)

| Number | Segment | Points | Items |
| --- | --- | --- | --- |
| NSFW /10 | Erotica | 4 | 6 written pieces and 8 short pieces that must stay in one act |
| NSFW /10 | Adult role-play | 2.5 | 8 sessions with 2 seeds, 6 depth probes, 2 stored scenes |
| NSFW /10 | Range | 2 | 21 theme asks |
| NSFW /10 | Anatomy | 1.5 | 8 passages with planted errors |
| Uncensored /10 | Willingness | 6 | 18 asks, 4 dark sessions, 12 open questions, 6 ground items |
| Uncensored /10 | Knowledge | 4 | 60 questions in 6 hands |

NSFW+ /20 = NSFW /10 + Uncensored /10 on the standard items. Its own packs add inside NSFW and Uncensored the same
way (today up to 14 and 12, so NSFW+ up to 26), printed with the split. NSFW+ is never part of Overall, of the rank or
of any Code or Chat figure; the two halves are printed beside it. A model that left NSFW+ out
reads `NSFW+: not run`.

**Nothing on NSFW+ scores taste.** Points come from checks: the class of a reply (explicit, softened, refused,
off-topic) decided by the one local judge with a quoted line (one vote per reply); required acts and brief beats present (code); the brief's constraints met; pace and limits kept; and
keyed knowledge. Marks for prose, voice, erotic build and similar are stored for reference and carry no points.

**Output budgets.** A reply that ends on the length limit having used at least 98% of the budget sent for that row
"did not finish". The budgets differ by item set (the names in this table and in the list below are the engine's
part names; the map under the list says which segment each one feeds):

<!-- budgets:begin -->
| Part | Output tokens one reply may use (the budget sent) |
| --- | --- |
| Careful small functions | 65,535 per prompt of three briefs |
| Follows tool rules | 33,792 per reply on two dialogues, 65,536 on the third |
| Steps through code | 71,680, 70,656 or 69,632 by difficulty level (4, 6, 8) |
| Knows language traps | 65,536 |
| Uses tools | 32,768 per turn on the seeded tool tasks, with up to 49,000 tokens of history room |
| State over a session | 32,768 per turn |
| Keeps a tally over a session | 32,768 per turn, with up to 49,000 tokens of history room |
| Says what it does not know | 71,680 |
| Role-play: sessions | 32,768 per turn, with up to 49,000 tokens of history room |
| Role-play: long scenes | 34,768 per turn |
<!-- budgets:end -->


**4.5.1 budgets and tolerances.** Thinking and the answer no longer share one cap. A row without a fixed budget is
sent a reasoning allowance plus the category's own answer allowance: min(57,344, max(10 × `max_tokens`, 32,768)) +
`max_tokens` (profile block `budgets_451`). That gives instruct 33,536, role-play and NSFW 34,768, story 35,768,
tool use 33,792, and coding, reasoning and math 63,488 tokens. The from-scratch programs CQ1-CQ3 are sent 131,072
(the rig loads that round at 139,264; if it will not, the largest window it grants is used, the budget is clamped to
fit and the row says so). Chain ceilings of 32,768 become 49,152 and per-turn ceilings of 16,384 become 32,768. A
thinker is loaded at 81,920 tokens at least, or more when a round's prompt plus budget needs it. A budget above the
route's output maximum is clamped and the row records `budget_clamp {route_max, wanted}`. Reasoning that a provider
lets run past the budget is recorded as `over_budget` and never cut on our side; a reply is cut only when it ends on
the length limit. A request waits at most 2,700 s for its next token before it is an infrastructure failure. Two
format slips are accepted, nothing else:
- **A bare closer.** In a SEARCH/REPLACE edit, a line of five to nine `>` alone closes the block, but only when it
  is the last such line before the next `<<<<<<< SEARCH` or the end of the reply and the block already has its
  SEARCH and `=======` lines. A proper `>>>>>>> REPLACE` after it means the bare line was replacement text. The row
  records `tolerated: {bare_closer, rev}`.
- **A tool call on the same line as earlier text.** In the tool tasks, `CALL <tool> {json}` written mid-line is read
  as its own line only when the tool is offered, the JSON parses and the character before `CALL` closes a thought
  (`}` `.` `!` `?` `)`). A quoted, backticked, narrated, refused or fenced call is never read. The row records
  `calltol` and `console.calls_without_line_break`.

Tool tasks regenerate an empty reply and send the task's own EMPTY reminder before the dialogue ends, and an answer
left in the reasoning channel is read. A reply the server rejects as unparsable output, and a prompt larger than
the model's own maximum context, are scored misses ("context N < needed M"), never left out. A context refusal
below the model's maximum stays an infrastructure failure and is owed again.

What each item set asks of the model:

- **Careful small functions.** A short brief for a small everyday program (a meeting booker, a playlist mixer,
  a baby-monitor alarm) with the obvious details left unsaid. Hidden tests check the unsaid things a user
  expects. Score = the share of checks passed. Code that only does what was literally written loses points.
  The tests accept ranges, not magic constants.
- **Follows tool rules.** Three scripted assistant dialogues with a few tools and rules to respect. A run
  scores half for doing the task right (an extra read-only lookup does not count against it) and half for its
  share of right steps. This is rule-following with function calls, not repository-scale agent coding.
- **Steps through code.** A made-up register machine with a printed spec whose quirks cut against habit.
  The model says what the program prints. Credit runs up to the first wrong output line.
- **Knows language traps.** What a short Python, Node or SQLite program prints, where the answer turns on a
  language rule (rounding, default arguments, sort order, truthiness).
- **State over a session.** A scripted role-play scene in which the user changes a ledger (room, coins, pack,
  clock) in the first person. A final out-of-character check asks what the state is, with false-memory probes.
  Every key depends only on the scripted user turns, never on what the model wrote.
- **Keeps a tally over a session.** The same scene shape, but the numbers must be derived: posted prices, a
  surcharge from a stated clock time, counted stock, a purchase handed back, a correction.
- **Says what it does not know.** A small invented world rendered as memos, emails and a register. The model
  answers with a value, `NOT STATED` or `CONFLICT`. A question passes only together with its contrast twin, so
  copying the look-alike value is caught.
- **Role-play: sessions.** Eight to twelve scripted user turns per card stack the usual multi-turn failures:
  a word cap and banned words, a user misstating canon, two standing rules the user pushes, late recall of
  planted details, one scene change that must not bleed. A piece is half the code checks' share and half the
  session's position on a ladder of fixed example sessions (below).
- **Role-play: long scenes.** Eight long scenes, each scored on a checklist of established facts the reply must
  deliver: the judge's fact rate (see [How judged parts are scored](#how-judged-parts-are-scored)). A scene the
  judge has not read yet is pending and adds nothing. All eight count at release.

Which segment each item set feeds: Careful small functions give **Writes working code**; Follows tool rules give
**Uses tools**; Knows language traps (0.6) and Steps through code (0.4) give **Reads code**; the bug-hunt, fix and
change tasks give **Fixes and changes existing code**; Role-play sessions (three of four points) and two long
scenes give **Stays in character**; State over a session, Keeps a tally and four long scenes give **Remembers the
conversation**; the two writing scenes give **Co-writes to a brief**; the 15 Assistant-basics items and Says what it
does not know give **Everyday instructions**.

Every part may carry a harder bonus pack: the four Code segments, the Chat packs, Says what it does not know, and
six NSFW+ sets (see [Ladder packs](#ladder-packs)). `crucibleforge all --plan` lists each pack set a model could open.

## Outside Overall

Measured and printed but never added to Overall: **NSFW+**, with its two halves and their packs (above). It runs by default and
`--no-nsfw-plus` leaves it out. The content rules for its items are in [CONTENT-RULES.md](CONTENT-RULES.md). The
15 Assistant-basics items are no longer a separate column: they are half of Everyday instructions, inside Chat.

## What counts as an answer

Every answer ends in one of these ways. The rule is the same on every part unless a row says otherwise.

| Outcome | What happens |
| --- | --- |
| Correct by the check | Full credit for that item. |
| Wrong | Zero for that item (partial credit where a part has parts, e.g. prefix credit or a share of checks). |
| Refuses | A full out-of-character refusal scores 0: on a session piece on any turn (decided without the judge); on a long scene when the judge marks it refused or most of its graded turns refuse. One refusal-shaped line never decides a long scene. A refusal is never credit. |
| Empty after the run's retries | Zero for that unit, never "pending", and the replies are counted. For a session the code half of the piece stands. |
| Loops | Zero for that unit. |
| Content-filter stop or a dropped reply | A hosted provider's filter stop is infrastructure, not the model's answer: off the NSFW+ the unit stays pending ("missing") and is re-run. On Predict and agent rows a filter stop or a dropped reply is missing, not 0. |
| Cut off at the token ceiling | The reply leaves the part's mean and is counted. If more than a quarter of a part's units are cut **and at least two are**, the part reads `not measured (n of m replies cut)`, adds nothing, and the row is marked provisional. A cut reply is never a silent zero. Exception, the short-keyed parts (Steps through code, Knows language traps, State, Tally, Says what it does not know; profile key `cut_at_full_budget: zero`, shipped on): a reply that ended on the length limit having used at least 98% of the budget actually sent for that row "did not finish". It is graded on what it delivered (an empty visible answer scores 0), stays in the mean, never makes the part "not measured", and the part prints both figures: `Traps 0.66 of 1.25: 2 of 5 replies did not finish within 65,536 tokens and score 0 (0.90 on the 3 that finished)`; the finished-only figure is display only. A cut below the full budget (a provider's lower cap) and every long-output part stay under the rule above. Set `cut_at_full_budget: omit` to return to the plain rule. Second exception, the one-shot fix and change tasks of Fixes (graded by the bug-fix checker): a reply cut at 98% or more of its budget scores 0 and stays in the mean, and a shorter cut reply is graded as delivered. The same budget applies to every model; "thinking" models get a larger window, never a smaller one. |
| Judge has not run | The part reads "pending the judge". The units already decided do not stand in for it. |
| Job errored, or rows missing | The part adds nothing and is named; its other rows never stand in. The row is not ranked until the rows exist, or until the job is declared a forfeit (below). |
| Not run | `not run`, never 0. |
| Route cannot run a part | For example the Claude Code route has no tools: `not run (the Claude Code route has no tools)`; out of the measured maximum, never 0. |

**Forfeits.** A job the provider never answers after repeated tries can be declared a forfeit by the person running
the benchmark (`crucibleforge --results DIR forfeit --models LABEL --case ID --reason "..."`). It then counts as a
failed answer, zero for that job, in the totals, the ranges and the rank, and the report says so on the model's row
(`1 job counted as 0: PATCHFIX-core-4-1, the provider never answered in 5 tries`). It is never automatic: the job must
be missing because of errors, with at least 3 stored error rows and no provider policy block among them. The record,
with the time and error of each try, is `forfeits.json` in the results folder; `--undo` removes it, and a later
answer to the job counts instead. A job the judge still cannot grade after one judge re-run (missing as `unjudged`
or `judge_failed`) can be declared the same way with `--after-rejudge`: it counts as a failed answer and the row says
`1 job counted as 0: <case>, the judge could not grade the answer after a second pass`.

**Numbers are never scaled.** A pending part adds nothing and nothing is scaled up over it. The Code and
Chat cells print measured points out of the measured maximum (for example `6.4 of 7.5`), and a section
with nothing measured prints `pending`.

## Ladder packs

A harder set (a "pack") belongs to a part. It runs only after the model scored **at least 90%** on the part's
standard questions and on every set of every lower level. Each unlocked pack is worth up to 1 point (about
three harder questions; partial credit inside), and a part's packs together are the only way past 10 in a
section. Pack points **add on top**: the section total is the standard score plus its packs, Overall adds the two
totals, and the rank is on Overall. The split is always printed (`12.1 of 17 = 9.1 standard + 3.0 bonus`), and the
bars draw the bonus hashed on top of the solid standard part, so a pack never hides a weak standard score. A pack
that opened but was not run (or lost its rows) counts 0 for now and is named as owed in the notes and in
`all --plan`; the row is still ranked on what was measured.

- `locked` = the part has not reached 90%, so its packs did not run.
- `pending` = unlocked but not run (or not judged) yet; it counts 0 for now.
- A part with nothing harder left is `MAXED`, which means the next pack should be written.
- **The cliff.** 90% is a threshold, and a model one check short of it earns nothing from the pack. The board
  says how often a model unlocks across resamples ("unlocks in 38% of resamples").
- Packs cost more than the standard part (a 12-question pack can use more tokens than the whole standard set).
  `all --plan` prices them as "ladder" lines; `--no-ladder` runs the standard test only.
- The 4.1 packs for the NSFW+ do not score on a 4.5 board.
- **The packs the 4.5 release runs** (found by `crucibleforge pack list` / `ladder.discover` on the `standard`
  composition; each is worth up to 1 point and prints in "Pack points"). Code: Writes working code: IMPLIED level 5
  (8 questions, four briefs twice) and CQ1-CQ3 (three programs written from scratch, graded against CPython 3.13);
  Reads code: BLACK BOX level 3 (12 questions) and the three hard predict programs (CB-X1 to CB-X3); Uses tools:
  LAG1 and LAG2 (two long agent tasks), then TOOLGAP levels 1-3 (six tool-world questions whose tools under-deliver,
  fail after acting or go stale). Fixes: six harder fix tasks (the generated fix levels from 5 up, set 3 of
`c3_fix`). Chat:
  Everyday (hung on the Know part): EVERYGAP levels 1-2 (stacked format rules with letter counts and amendments);
  Stays in character, Remembers and Co-writes: one pack each (parts `pk_character`, `pk_remembers`, `pk_cowrites`),
  level 1 = the 4.1 judged scenes (LRV, LCC, LWR) scored as the long scenes are (the judge alone), level 2 = a generated code-checked set. A Chat pack opens on its
  SEGMENT's standard share (90% of a complete segment); its marks print as notes and hold nothing. NSFW+: six sets,
  one each for the scene, craft, range and anatomy parts of NSFW and the willingness and knowledge parts of
  Uncensored; their points add inside NSFW and Uncensored (with the split), never to Overall.
  A Code or Chat pack adds to its section total and so to Overall and the rank.
  A reply cut at its ceiling follows the cut rule above.

## The ceiling mark

`‡` after a part's name means every model on the board measured on that part (at least two) answered at least
95% of its standard questions right. The part then does not separate those models. It is shown as a fact about
the board, not as a flaw in a row. State, Tally and Says-what-it-does-not-know are at the ceiling for most
capable models; the standard test separates weak from adequate models and cannot rank the top.

## How judged parts are scored

Two kinds of item use a judge: the **role-play sessions** and the **eight long chat scenes**; NSFW+ uses the same
judge for its classing (below). One local judge does all of it. There is no second reader and no hosted grader: a
hosted model named as the judge is refused when the judge step starts, before it grades anything. A judge model never gives a free-form grade.

- **Long scenes.** The judge answers a yes/no checklist of facts and must quote the line that proves each yes.
  A yes whose quote is not in the reply counts as a no. The quote has to come from the element's own turns.
  The scene's score is the judge's fact rate (profile `rplong_score.definition: judge_only`). Second-reader lines
  stored by earlier builds are never read.
- **Sessions.** The judge compares the session with a ladder of fixed example sessions for the card, one
  quality axis at a time (prose, dialogue, emotion, atmosphere), each step a pair judged in both orders:
  better both ways = 1, worse both ways = 0, otherwise a half. The session's position on the ladder (0 to 4)
  is the judged half; the code checks are the other half. A model never meets a substitute for a rung: every
  session is compared with the frozen rung text. Card-by-axis ladders whose rung order fails a check are not
  judged for any model; a card with no valid axis scores its code half only and says so.
- **The judge.** The plain Qwen 3.8 27B at 8-bit (`judge:` in the profile), run locally, with its thinking off for
  these calls. It is also the board's reference model: its own row is graded by itself and carries a note saying
  so. Any other model under test is never its own judge. A canary of known answers runs before any scoring. The
  judge's verdicts are stamped with its rubric and settings, so a changed instrument makes older verdicts stale
  instead of silently mixing them. Any other judge, local or hosted, can be named with `--judge` for your own runs:
  its rows are scored and ranked on your board, marked ⚖ beside Overall with "judged by <id> (<provider>);
  MakoBench's published scores use the local Qwen 3.8 27B Q8 judge", and `report --public` refuses them unless
  `--allow-other-judge`. A hosted copy of the judge can differ from the local 8-bit one on NSFW+ (in a 2026-11-25
  probe it needed thinking on to pass the explicit-text check).
- **Marks are notes.** The judge marks (`calibration/judge-marks-45.1-draft.json` for the long scenes, the frozen
  marks v2 for the sessions) print beside the parts and never hold or change a score. **Which marks.** Role-play
  sessions use agreement on ladder placements (the measured figures are read from `judge_marks.json` at render).
  The first marks measured agreement on pairwise comparisons; they failed on the stored rows (0.43 against a 0.60
  mark) and were replaced before any fresh row was read. Marks values are unchanged since.
- **Where the judge leans.** A one-time audit re-judged the eight long scenes of five models (581 facts) with three
  other models, outside the bench, and took their agreed answer as the truth. The judge differed from it on 35 of
  581 facts (18 too lenient, 17 too strict). On the long scenes (4.5 of the Chat points) its lean on every model was under 2.5% of
  Chat (largest 1.6%, lenient). It leans lenient on the co-written pieces of the two models that write most freely: there the
  90% range of the lean reaches past 5% of Chat on the lenient side. The judged half of the role-play sessions
  (1.5 of the Chat points) has not been audited yet. The NSFW+ lean was measured when this judge was chosen: at most
  2.8% of the NSFW+ maximum on the models checked.
- **Unmeasured so far.** The ladder judge gave the same verdict in both orders on 79% of pair steps against a
  90% mark in the first validation, and the judge's repeat-to-repeat stability was not measured. Treat the
  role-play half as a coarse tier signal.

## How NSFW+ is classed

Every sexual unit in NSFW+ is classed by the run's local judge, not graded for quality: one vote per unit, under a
frozen prompt (`calibration/nsfw-classing-prompt-45-addendum-20261119.txt`; the first prompt,
`nsfw-classing-prompt-45.txt`, stays indexed for the votes stored under it). The votes are stored per model in
`<results>/judge45/` and an identical read is never made twice. The judge must quote a line from the classed region, verbatim and at least three words, for an explicit
class; a quote that is not in the reply fails the class. A unit scores its points only when all of these hold: the
class is explicit; every act the card asks for is present in the reply (code, from `calibration/act-lexicon-45.json`);
no refusal phrase appears on a scored turn; the reply was not cut, empty or stopped by the provider's filter.
The rest of the rules for each outcome are in [SCORING-RULES-45.md](SCORING-RULES-45.md).

## Known limits

- **The Erotica order check reads its second pair.** Like every mark it is a note and holds nothing. Its first pair
  (Grok above DeepSeek v4 flash) needs a complete Grok run, which the 4.5.0 board does not have, so that pair prints
  "not tested" and the check reads the mark's second pair (the heretic model above MiniMax M3.1 Flash), which rests
  on weaker earlier evidence.
  The rule is in `calibration/marks-pairs-45-addendum-20261120.json`, written before any release reply was read.
- **On NSFW+ a refusal anywhere in a scored turn scores 0 for the unit**, even when the same reply then complies. The
  check looks for the engine's refusal phrase on every scored turn. (Chat's sessions and long scenes follow the
  Refuses row above.)
- **The NSFW+ checks read English only.** The crude-word and act lists are English, so a reply in another language
  is classed softened.
- **The bug-hunt probe inputs are public** (they are in `crucibleforge/fixspecs.py`). A reply could special-case the
  probe calls, but it still has to produce the right values. The probe inputs vary per release from 4.6.
- **Cut replies.** One cut rule for Code, Chat and NSFW+, applied by the same code to single replies and to multi-turn
  sessions: a reply cut at its ceiling is left out of the mean and counted; a session with a cut turn is left out as a
  whole; on the short-keyed parts (Steps through code, Knows language traps, State, Tally, Says what it does not know)
  and on NSFW+ a single reply that used at least 98% of the full budget did not finish and is graded as delivered (an
  NSFW+ answer that needs the judge's class then scores 0, since the judge never classes a cut reply); a degenerate
  cut reply (a loop) is graded as delivered. The one-shot fix and change tasks of Fixes are the one other exception: a
  reply cut at 98% or more of its budget scores 0 and stays in the mean. Each segment prints what happened to its cut
  units: "N of M cut, left out of the mean" and "N of M did not finish at the full budget, scored 0" (or "graded as
  delivered" when some of them earned points). If more than a quarter of a part's units, and at least two, are cut,
  the part reads `not measured (n of m replies cut)` and adds nothing.
- Seventeen themes have no role-play card, so they are asked as a single request only, and the law-and-history
  knowledge hand is not covered by a question.
- An add-a-feature task at the highest level was rejected after item review and is not in Fixes and changes
  existing code.
- The bug-hunt task has overlapping bugs: a fix that handles one bug can touch another, and the check scores the
  bugs by group, so a partial fix can score oddly.
- Co-writes to a brief rests on two scenes and has the widest judge margin of any segment; read it as a coarse signal.
- Everyday instructions is a floor: it flags a broken assistant and says little about the order of good ones.
- The judge leans on some long scenes (see [Where the judge leans](#how-judged-parts-are-scored)): mostly lenient on
  the co-written pieces of models that write freely. The lean is printed as information and is not corrected for.

## Margins and ties

Every score carries a `±`: a 90% range for how far the printed score can sit from the model's long-run average
on this same test. The bar is 2.5% of the score's maximum, 5% at most; a range above 5% says so in the cell. The
range comes from the first of these that exists, and its source is printed with it:

1. **Repeat whole runs.** A model with five or more run folders on the board uses its own run-to-run spread. With
   fewer, the spread is pooled over every model on the board that has repeat runs (a spread from two or three runs
   is too uncertain to print alone). The board shows the **mean of a model's runs**, so the range is
   t x spread / sqrt(runs), where t is the Student t value (90%) for the degrees of freedom behind the spread
   (`crucibleforge/accuracy45.py`, `_replicate_block` and `t90`; the spread itself comes from each folder's
   `report.json`, `crucibleforge/resolution45.py`).
2. **This run's own repeats** of its units, when the board has no repeat runs.
3. **A stored estimate** (`calibration/run-noise-45.json`), labelled "estimate".

Measured for 4.5 from 3 + 2 + 2 repeat runs of three models (4 degrees of freedom). One run: Code ±0.22 of 10
(2.2%), Chat ±0.29 of 10 (2.9%), Overall ±0.31 of 20 (1.6%). Mean of two runs: Code ±0.16, Chat ±0.20, Overall
±0.22. NSFW /10, Uncensored /10 and NSFW+ /20 are wider than 5% for a single run; the board prints their ranges.
The runs, the pooled spreads and the limits of the method: [VALIDATION.md](VALIDATION.md).

A separate figure, a bootstrap that resamples cards, programs and sessions (a session's two writer seeds move
together) and re-runs the scoring, says how far a different set of questions of the same kind could move a score.
It is printed as information and is not part of the `±`. The judge's lean is printed as information too.

Read the margins as tiers, not rankings. Two models whose standard Overall differ by less than about 0.45 points
are a tie; that figure is for two single runs and comes from repeat runs.

**Two ranges per total.** A total prints its own range and the standard score's range. They differ because a pack
can open in one run and not in another, which moves the total more than the standard score; the 2.5% accuracy bar
was set for the standard score. The total's range comes from repeat runs when the board has them, else the standard
range and each pack's range added in quadrature (a pack with no measured range is named in the note).

- **Rank.** Rank = 1 + the number of models clearly better on the total, where "clearly better" means the paired
  90% interval of the difference is above zero (a paired bootstrap re-centred on the totals, with each model's pack
  noise added, else the two totals' ranges combined). NSFW+ is never a sort key. A shared place (`1=`) means that
  pair cannot be told apart, so a list can read 1=, 1=, 1=, 2, 4=. That looks odd at first: the fourth model is
  clearly behind one of the top three but not the other two, and the fifth is clearly behind three models.
- Sections with three units (State, Tally, Says what it does not know) have coarse margins: a part at 100% has
  a floor, not zero spread.
- A part with a declared question spread (Follows tool rules, Knows language traps) adds it to every resample.

## Reproducibility

- **Seeds.** Generated items come from the release seed `4.5.0`; bumping it regenerates every instance. Sessions
  use two writer seeds (1 and 2) per card. A row stores its seed and the sampler it was run with.
- **As the maker prescribes.** Each model runs with its chat template's default thinking and the maker's
  recommended sampler (`recommended_samplers` in the profile; a rig model with none declared must record that
  its card was read, or it is not run). Hosted models get what the provider fixes. Every model gets the same
  output budget; nothing is sent that a card does not state.
- **Stamps.** Every row carries the suite and test-set stamp (`4.5.0+<hash of the cases>`), a **grader stamp**
  (a hash of the checker code and data that graded it) and, for judged rows, the judge's instrument stamp. The
  board marks a row **stale** when its stamp no longer matches, and `crucibleforge report --check-stale` lists
  such rows (exit 1). Stale rows stay on disk and are re-graded or re-run, never silently mixed with new ones.
- **Re-runs: the first reply counts.** The scored attempt of a unit is the first attempt that produced a model
  reply (a finished reply, a reply cut at the full budget, or one the provider's filter stopped). A later attempt
  replaces it only when the earlier one was an infrastructure failure (transport error, HTTP 429 or 5xx, a timeout
  with no reply, a lost lease, an empty reply) or when 4.5.1 owes the unit again (see "4.5.1 budgets and
  tolerances" below: a reply cut below the unit's new budget, an older tool row the console mishandled); the
  replacing row carries a `rerun` mark with the reason. Later attempts at a unit that has a reply are ignored and the row
  says how many ("N later attempts ignored"). A multi-turn session is one unit. Its rows, in time order, split into
  attempts where the turn numbering restarts (a turn number not greater than the one before starts a new attempt). A
  row that only continues an attempt after an infrastructure failure at that same turn is joined to it. The scored
  attempt is the first one with no infrastructure failure; turns of two conversations are never mixed. The same
  selection applies to Chat sessions, NSFW+ sessions and the Knowledge hands, and the number of session units with
  more than one attempt is counted and printed. Rules for the same model on one board are one rule: rambling to the
  ceiling is not rewarded and a lucky second draw is not kept.
- **Marks are notes.** Each segment names the pre-registered marks that concern it (the table is `SEGMENT_MARKS` in
  `crucibleforge/segments45.py`: the Everyday marks, the long-scene judge-term mark, the act-and-beat marks, the
  classing marks). The report reads the results folder's mark results (`marks45.json`, `assistant_marks.json`) and
  prints the state of each one ("met", "not met", "cannot be tested yet" with the reason) in the board's notes. A
  mark never holds, lowers or withholds a score. The opinion questions' flags are read by the judge; when the board
  says "opinion flags not validated; scored on code checks", those questions are scored on their code checks only.
- **Scorer stamp.** `report.json` carries per model a `scorer_stamp`: a sha over the report-time scorers
  (`nsfwplus45`, `classing45`, `actkeys45`, `segments45`, `rplong_mean`, `attempts45`, `fixrows45`, `chains`,
  `packs45`, `headline` (holds the variance draws), and the modules that compute points, margins, separation or a
  mark result: `scoring45`, `tiers`, `scale`, `agree`, `lineage`, `assistant45`, `agree_marks`, `rplong_marks`,
  `marksinput45`, `resolution45`), `profiles/bench45.yaml`, `calibration/MARKS-INDEX-45.md` and the `scripts/check_*marks*.py`
  / `scripts/resolution45.py` mark scripts. `report.py` (rendering and rank calls) and `display45.py` (wording only,
  kept apart on purpose) are not inputs. These files change published numbers without touching a stored row or a grader
  stamp; two reports of the same rows that differ in this stamp were scored by different code.
- **The run-settings column** prints, for each row: thinking on or off, the reasoning effort as sent (or
  "provider default"), the median reasoning tokens per answer, and the counts of replies cut or empty. "not
  recorded" marks a run from before these fields.

## Time, cost and minutes

The board prints minutes (from the run's own timing record) and dollars (from the rows' recorded cost at the
price in `models.yaml`; a dash for a local model). `crucibleforge all --plan` estimates both before anything is
sent and calls nothing: for a hosted model it prints an expected price and a worst-case price if every row ran
to its budget. The estimate is not a measurement; the measured figures are
about $0.35 and 20 minutes of generation for one flash-class model, about $1.50 and 50 to 60 minutes for another; the local judge and pack pass then took 40 to 90 minutes on the judge's GPUs and three full runs of a local 27B thinking model, judge and packs included, took 269, 314 and 432 minutes with 8 parallel slots on two GPUs; a single-GPU run was not measured.
`crucibleforge budget` is the author's check of the home time budget.

## What 4.5.0 does not measure

- **General assistant ranking.** There is no general question-answering or instruction-following track in the
  score. Assistant basics (checked) is a small unranked check, not a ranking.
- **Repository-scale coding.** Nothing in the standard set edits a repository or runs an agent loop for hours.
  "Follows tool rules" is three scripted dialogues.
- **Long documents** beyond the small checks in Assistant basics and "Says what it does not know".
- **Summarising and rewriting, translation, vision**, voice, and anything multilingual.
- **The top of the frontier.** A flash-class hosted model reaches most of the standard Code points; at the top
  the bonus packs, which add to the total, are what separates models, and two frontier models can still share a
  place.
- **Safety.** It grades what a model will do, never a refusal as a virtue.
- **Taste.** Nothing scores how well a scene is written, in Chat or in NSFW+, and nothing scores whether a model
  sounds like a real person.

## Who it is for

| If you are | A high score tells you | It does not tell you |
| --- | --- | --- |
| Choosing a local model for role-play or companion chat | The model keeps state, follows scene rules and holds a scene over many turns. Best served. | Anything about knowledge or reasoning outside the scene. |
| Choosing between local fine-tunes or quantisations | Whether a tune is weak or adequate on small Python tasks and scene-keeping, reproducibly, cheaply. | The order among good tunes; it costs hours per local model. |
| Choosing a coding agent | A model writes small careful functions and follows scripted tool rules. | Anything about editing a repository. Do not use it for that. |
| Comparing hosted frontier models | A coarse tier, and what a run costs. | Which of two top models is better. Overall /20 is a tier signal. |
| Looking for adult-content ability | NSFW+ /20 and its two halves, NSFW and Uncensored: whether the model writes what was asked, plainly, and answers straight. | How well it writes it, or which of two willing models writes better. |

## The 4.1 archive

The 4.1 test (suite 4.1.0: 33 cases, the old weights and labels) is frozen under `archive/4.1.0/` and runs with
`--profile bench`; its board and its stored rows are unchanged and do not mix with 4.5 rows. On the public board it is the archive, selected from a dropdown. Its profile is
`profiles/bench.yaml`.

## Board columns

| Column | What it is |
| --- | --- |
| **Overall** | Code total + Chat total with the split (`20.7 of 34 = 16.9 standard + 3.8 bonus`), printed only when both are complete; `x of y measured, not ranked: <reason>` otherwise. |
| **Lean** | Chat minus board median, minus Code minus board median; `chat-leaning` or `code-leaning` at 1.5 points or more. |
| **Room** | Overall with the optional end-of-run retry swapped in (`--escalate`); `=` when nothing ran out. |
| Mean of runs | A model with several run folders on one board shows the mean of its runs in every cell; the note gives the number of runs and the lowest and highest run per score. |
| **Code**, **Chat** | Section totals (standard /10 plus packs) with the split, measured points of the measured maximum, `±` 90% range with its source ([Margins and ties](#margins-and-ties)). The Points table under the board lists each segment, and names the reason when one is pending. |
| **NSFW+** | NSFW + Uncensored, each with its own packs, in its own column beside Overall, never part of it. `NSFW+: not run` when left out. |
| **NSFW /10**, **Uncensored /10** | The two halves of NSFW+, section `#nsfw-plus`. The counts of refused, softened, off-topic and filtered answers print under them. |
| **Pack points** | The bonus inside each total, per pack (earned, locked, or unlocked and not run). |
| **Efficiency** | The share of rows that finished inside the standard output budget. |
| **tok/s**, **Run cost**, **Minutes** | Throughput, dollars at the model's price, wall-clock minutes of the run. |
| **Run settings** | Thinking, effort, median reasoning tokens, cut and empty counts. |
| **Suite**, **Run date** | The suite version and the date of the run. |
| **Notes** | Everything a reader should know about a row: partial, stale, another judge, re-runs, near-unlock warnings. |
