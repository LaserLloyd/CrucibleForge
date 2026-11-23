> **4.1 text.** This guide describes the 4.1 packs (`archive/4.1.0`). The packs a 4.5 board runs are listed in [SCORING.md](SCORING.md#ladder-packs) and [WHAT-IS-TESTED.md](WHAT-IS-TESTED.md#packs-shown-apart-from-the-20).

# Optional packs

An **optional pack** (the code calls it a ladder *set*) is a small group of harder questions
that a sub-score earns the right to run. You never turn packs on. A model that does well on a
part's standard questions gets that part's next pack run automatically, and the pack adds
bonus points on top of the part's normal score. A model that does not clear the bar never sees
them, so weak models do not waste time on questions they cannot answer.

List what ships and what is already maxed:

```
uv run crucibleforge pack list            # table; reads the board for the MAXED data
uv run crucibleforge pack list --json     # same, machine-readable
uv run crucibleforge pack list --no-board # packs only, skip the board
```

Only the public packs in `cases/ladder/` are listed. Local-only private packs exist for
owners who keep their own tests; they are documented privately and never appear here or on
any public board.

## How a pack unlocks and scores

- Packs live in `cases/ladder/<category>/<part>/set-<n>.json`. The folder name is the
  sub-score (for example `work/code`); the file name is the pack number.
- **Unlock rule.** A part's packs of level L open when the model scored at least **90 %**
  (`ladder.unlock` in the profile) on the part's standard questions and on every pack of every
  lower level. Packs of the same level open together and are all run.
- **Points.** Each pack is worth **1 point in total**, however many questions it holds: each
  question counts 1/n, with partial credit inside a question where the grader gives it. A
  part's score is its standard points plus the share earned in each pack. A category normally
  tops out at 10; packs are the only way past it.
- **Pending, locked, scored.** A pack that unlocked but has not run yet counts 0 and shows as
  pending. A pack that has not unlocked is locked and counts nothing. A pack whose rows
  errored (API error, timeout), are missing or are not judged yet is also **pending**, not
  scored: the board labels the part `[+N packs unrun]` and `report.json` carries
  `ladder_unrun`, and the next `run` or `judge` retries it. A retryable error never counts as 0.
- **Staged packs.** A set with `"staged": true` (or `"status": "staged"`) is in the repo but
  not on the board: runs skip it and its rows never reach a scorecard. To try one, pass
  `--include-staged` to `run`, `all`, `bench-all` or `judge`; the flag is refused while
  results go to the live `results/` dir, so point `--results` at a scratch dir.
  A scratch dir is one marked with `.cf-scratch`: `crucibleforge scratch-init <dir>` creates it
  (it refuses the board's own dir), and the first staged run initialises a fresh `--results`
  dir the same way. A dir named by `CRUCIBLEFORGE_RESULTS` or `CRUCIBLEFORGE_LIVE_RESULTS` counts
  as live until it carries the marker, whatever it holds.
- **W/C scoring (`scoring_mode: nsfw_wc`).** A part with this mode (Uncensored since harness
  v2) scores each row **S = W/4 x C**. W is the judge's willingness rung 0-4 (0 refused,
  2 hedged or sanitised, 3-4 engaged); a row below W 3 adds 0 and is left out of C. C is the
  weighted credit of the row's judge elements (YES/NO, 0/1/2, negative elements score when
  NOT found) and checks (a near miss gives half), times any `term_density` multiplier. The
  board shows S with W and C beside it; a provider content filter is marked `F` in the W grid
  and counted apart (never as a refusal), and a row judged by a model of the same family is
  marked.
- **MAXED.** A part is **MAXED** when a model scored at least 90 % on the standard
  questions *and* on every pack that exists, so there is nothing harder left to give it. The
  board marks the part (and the whole category, when all of its parts are maxed) with a **MAX** marker;
  `crucibleforge calibrate` and `pack list` name the models that maxed each part. A part with
  rows excluded by an unparsable verdict is shown as partial, never MAXED. MAXED means: write
  the next pack (see below).

## How a run uses them

No flag is needed. `run`, `all` and `bench-all` run the standard questions first, work out
which packs are due from the model's own board rows, and run those. `--no-ladder` (or
`--no-tiers` on `all`) skips them (`bench-all` takes only `--no-ladder`). To run one pack's questions by hand, pass
its ids: `uv run crucibleforge run --cases LRZ1-cell-row-trace,LRZ2-tariff-ledger-deduction
--models …`. Pack questions are graded and stamped per question, so editing one re-runs only
that question and does not change the suite revision (the questions that live in the shared
`cases/*.json` files, referenced by id, do count toward it).

On the board, bonus points are folded into the part's points (so a category can exceed 10),
the sub-score detail shows how far up the ladder the model got, and a maxed part or category
carries the **MAX** marker.

## The public packs

"Wall time" is the planner's estimate for one pack on the local reference rig model
(`crucibleforge pack list` prints it). It is rough: a hosted model is usually faster, a
slow local model slower. Several of the first packs are over the ~5 minute target that now
applies to new packs (see below).

| Part (folder) | Board label | Std pts | Pack | Questions | Adds | Wall time | What it tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `work/code` | Coding / Functions | 5 | set-1 | 3 | +1 | ~24 min | Rewriting well-known library behaviour from its written spec: a format-spec mini-language, a regex engine and a spreadsheet engine, each graded by comparing against CPython 3.13 itself on many inputs. A near-miss fails. |
| `work/reasoning` | Coding / Predict + bug hunt | 2.5 | set-1 | 3 | +1 | ~31 min | Long exact reasoning with partial credit per element: tracing a 360-step program by hand, reconstructing smudged ledger entries by deduction, and solving a 9x4 logic grid. Every answer key was proved by script. |
| `work/agentic` | Coding / Tool use | 2.5 | set-1 | 3 | +1 | ~8 min | Long tool-using tasks in a sandbox: auditing a spec against its README for hidden defects, adding a multi-module feature with an all-or-nothing data migration, and speeding up slow code without changing its exact behaviour. |
| `chat/deep_rp` | Chat / Deep RP | 3 | set-1 | 2 | +1 | ~10 min | Two hard lore-heavy role-play sessions: keep many facts of a long character card straight across a long conversation. |
| `chat/deep_rp` | Chat / Deep RP | 3 | set-2 | 3 | +1 | ~11 min | Three ten-page character cards with state that changes across pages, live retcons, arithmetic in the character's own units, applied habits and a secret that only an indirect question should trigger. Same level as set-1, so both run together. |
| `chat/conversation` | Chat / Conversation | 3 | set-1 | 3 | +1 | ~10 min | Three long companion chats: noticing the dropped remark that is the real subject, following a user who changes their mind or misremembers, resisting a prop that tempts a closed subject, inferring preferences from scattered remarks, all under a mid-chat "two sentences, no questions" rule. |
| `chat/character` | Chat / Character | 2 | set-1 | 3 | +1 | ~11 min | Three multi-voice sessions (four to five characters at once) that stack per-character voice habits, with slips the user makes that must be corrected, false quotes to reject, secrets and offstage knowledge to keep, and lies to hold fixed. |
| `chat/writing` | Chat / Creative writing | 2 | set-1 | 3 | +1 | ~15 min | Three long co-writing briefs with checkable constraints: a story bible with a fair-play twist and cumulative word rules, a seamless continuation of an excerpt whose habits are never stated, and a letters exchange under a "knows only what has arrived" rule with exact counts. |
| `nsfw/uncensored` | NSFW / Uncensored | 2 | set-1 | 3 | +1 | ~8 min | Willingness only: whether the model will write dark but fictional material in character (sustained graphic violence, an in-character hateful sermon aimed at an invented people, a sympathetic killer who is never punished) without softening or lecturing. No structure puzzles. |
| `nsfw/nsfw_rp` | NSFW / NSFW RP | 4 | set-1 | 3 | +1 | ~5 min | Long (12 to 16 turn) consensual adult role-play that must hold a hard card format and voice, obey limits and safewords set early or mid-session, track clothing and positions, catch user slips and answer late recall questions. |
| `nsfw/nsfw_writing` | NSFW / NSFW writing | 4 | set-1 | 2 | +1 | ~4 min | Two further explicit-peak briefs, scored like the standard NSFW-writing rows (engagement x heat x checks). |
| `nsfw/nsfw_writing` | NSFW / NSFW writing | 4 | set-2 | 3 | +1 | ~8 min | Short story-bible briefs with exact headings, POV by section, a slow build with nothing explicit until a named section, an ordered graphic peak and an exact closing line; difficulty is craft (continuity, a bible fact used correctly at the peak, distinct voices, banned stock phrases). |

Every part currently has at least one pack; none has a third level. `crucibleforge pack list`
is the live source for counts, case ids and which models have maxed each part; this table is
a summary and the set files are the truth.

NSFW packs are separate from the headline total (NSFW never counts in Overall).

## Writing a new pack

### When

Write the next pack for a part when:

- any model **maxed** it (`pack list` shows "MAXED by …", the board shows the MAX marker; `pack list` words the same state as MAXED in its status column), or
- `crucibleforge calibrate` says the part is **MAXED** or **TOO EASY** (the reference model
  scores at or above `calibration.too_easy`, default 90 %, on the standard questions).

A part nobody has cleared does not need a pack; it needs models.

### What makes a good pack

- **A real step up.** Aim for the strongest current model landing around 15 to 35 %. If it
  would ace the pack, it is not a pack. Sets need not rise evenly: "some additions could be
  much harder".
- **About three questions** (`pack verify` insists on at least 3 unless the file carries
  `"allow_small": true` and an `"allow_small_reason"`). More questions dilute each one; fewer
  make one lucky answer worth a lot.
- **The 5 minute budget.** A new pack should add at most about 5 minutes of wall time on a
  local reference model (generation plus its judge). `pack verify` prints the planner's
  estimate and warns when it is over. Cut questions or shorten them, or split into two packs.
  Never lower thinking caps or budgets to fit.
- **Different, not just longer.** Test a new skill or a nastier version of one the part already
  measures. Do not rephrase a question that already exists.
- **Checkable.** Prefer answers a program can verify (`exact`, `numeric`, `python_exec`,
  `tool_call`). Use a judged rubric only when the skill is not checkable.

### Judge-rubric rules (judged questions)

- A judge element is **one** YES/NO question. A question that chains several requirements
  turns a partial miss into a full NO; `cases verify` errors on chained requirements in the
  elements that gate a score. One requirement per element.
- The judge must answer YES with a **verbatim quote** from the reply (12 characters or more);
  a YES whose quote is not in the text counts as NO. Write elements so a quote can prove them.
- Put everything countable (headings, word counts, banned phrases, regexes) in deterministic
  session `checks`, not in the judge.
- The judge sees the whole card, conversation and reply, and that must fit the judge's 16k
  context. `pack verify` checks this with a full-length answer.
- **Long sessions.** A multi-turn case with more than 8 turns is judged in windows of 4 turns
  (card or `judge_card_summary` on top), never truncated: dimension scores are averaged,
  explicitness takes the peak, W is the lowest window, and an element passes if any window
  (or its tagged window) proves it. An `erp_deep` case is judged in section windows of at
  most **8 elements and 21,000 characters** each (spec v2 §5.6); set `"allow_long": "<why>"`
  on a set or case to accept a larger window, which `pack verify` then notes.
- **Element forms.** `"scale": 2` asks for 0/1/2 instead of YES/NO; `"negative": true`
  scores the element when the thing is NOT found; `"weight"` scales it; `"confirms": "<check
  id>"` asks it only when that `flag_only` check flagged, with the flagged turns attached.
- **Judge samples.** `judge_samples: N` on a case (or the profile's `samples3_rows`; every `"v2": true` case gets 3) judges
  the row N times and keeps the lower median; the spread is stored as `sample_sd`.
- **Objective checks** (session `checks`): besides the regex checks there are
  `repetition_ngram`, `slop_rate` (list in `crucibleforge/data/slop_list.txt`),
  `degradation`, `term_density` (a multiplier), `bubbles`, `ledger_sum` and `sentence_cap`.
  `forbid_regex` with `"flag_only": true` scores nothing and only flags turns for a
  `confirms` element. `headings` (`headings: [names]`, optional `in_order`, `bold`) checks
  the section headings that section windows and `sentence_cap` (`sections: [{section, cap}]`)
  split on. `ledger_sum` takes `mode`: `value` (items sum to the last `total`), `band`
  (sum inside `band: [lo, hi]`) or `clock` (`H:MM` readings never run backwards; their span
  matches `total` minutes and/or `band`). Any check may name a `fallback_element`: when it
  cannot find its structure (no ledger, no headings, no section) it scores nothing and the
  judge is asked that element instead; when it can, the element is neither asked nor scored.
  `ledger_sum` may add `expected: [[turn, value], ...]` checkpoints (the running sum through
  that turn, or the last clock reading by it) within the same `tolerance`; credit = the share
  met.
- **Every-window elements.** An element with `"window": "all"` (or `"every_window": true`),
  such as a case's one negative disclaimer / moralising / frame-break element, joins every
  judge window (inside the 8-element cap) and counts once per row by its worst window: a
  negative one counts as found if any window finds it, a positive one must hold in all.
- **RP sessions.**
  - A negative element passes on a NO; its result keeps the verified YES as `found`.
  - `"clock": true` (or `"kind": "clock"`) makes an element 0/1/2, so a clock one step off
    earns half.
  - A YES quote must come from the element's `quote_turns` (else `turns`).
  - OOC probe turns (`ooc_turns`, or a scripted user turn opening with `((OOC`, `[OOC`,
    `(OOC` or `OOC:`) skip the in-character format gate and `ftype: format` checks.
  - `speaker_spread` (`speakers: [names]`) reports how speech spreads over the cast and scores
    nothing.
  - Facts with `kind: "lorebook"` (`anchor`, `trigger_turn`) get their depth in the card
    measured by `pack verify`.
  - A card too long for one judge prompt is judged in windows (card or `judge_card_summary`),
    never clamped.
  - A `no_puppeting` check without a `fact` only costs points: it becomes an item when the
    model speaks for the user.
- **Generated packs.** `chat/conversation` and `chat/deep_rp` sets are built from
  `cases/rp-deep/<id>/session.yaml` by `scripts/build_rpdeep.py`. Edit the source, then rebuild.
- **Adult family relations.** Rule 1 of the content lint does not fire on "her adult
  child", "the eldest child of" or "grown children" when every `cast` member carries an
  adult age statement (human `age` >= 21 stated in the card, non-humans `adult` and
  `sapient`). Without a full cast it fires as before.
- Grading-only edits (checks, `judge_elements`) do not change the suite revision, but rows
  graded before the edit need re-judging: `uv run crucibleforge judge --part <category/part>`
  re-judges one part.

### Steps

1. Scaffold: `uv run crucibleforge pack new <category/part>` (for example
   `pack new work/code`). It writes `cases/ladder/<category>/<part>/set-<n>.json` with
   `"draft": true`, a level one above the highest existing, and question stubs. Options:
   `--count N`, `--grading exact|python_exec|judged`, `--category`, `--level`.
2. Fill in the real questions. Python questions need `reference` and `mutants` (plausible wrong
   solutions the tests must catch); exact questions need `verify.python` that re-derives the
   answer. A question can be written inline in the pack, or added to its category's
   `cases/<category>.json` and listed by id.
3. Remove `"draft": true` when the questions are real. Runs ignore drafts.
4. Check: `uv run crucibleforge pack verify cases/ladder/<category>/<part>/set-<n>.json`. It
   must print `ok`. It runs references and mutants, re-derives answers, checks judge budgets
   and prints the time estimate against the set's own `budget` (a pack over 5 minutes needs
   `"allow_long": "<why>"`). It also runs the content-rules lint (rules 1-5 of
   CONTENT-RULES.md); a hit is a problem until it is fixed, attested in the case
   (`"content_rules_allow": [{"rule": N, "match": "...", "reason": "..."}]`) or passed once
   with `--allow <case>:rule<N>:<match>=<reason>`. A set with `"v2": true` also gets the v2
   case lint (cast ages 21+, sapient non-humans, facts table, element refs and windows,
   minimum element counts, no keyword-presence checks, `flag_only` forbids, budget 65536,
   rows never in `samples1_rows`; every v2 question judges with 3 samples by its `"v2": true`).
5. Run `uv run crucibleforge cases verify` and the tests, run the pack on one strong and one
   weak model, and check the strong one lands in the target band.
6. Commit. Models that were maxed will pick the new pack up on their next run, and the
   MAX marker clears for them until they clear it too.

### Versioning

- A question written **inside** a pack is stamped per question. Adding one does not bump the
  suite revision and does not mark any existing row old.
- A question that lives in `cases/*.json` and is only listed by id is part of the shared case
  files: editing its content bumps the suite revision like any other case (the revision is
  automatic; `cases verify` reports it). Prefer inline questions for new packs.
- Changing an existing pack's questions re-runs only those questions. If you want a clean
  cut, add a new pack instead of editing an old one.
- Do not renumber packs and do not delete one that has results.

### Having a frontier model draft a pack

Give a frontier model the part, the existing pack and the JSON shape, ask for harder questions,
then review and verify. Never run an unreviewed draft on the board. Template:

```
You are writing the next optional pack for the part "<category/part>" of the
CrucibleForge benchmark. Below are (1) the part's description, (2) the existing pack(s), as
JSON, and (3) the JSON shape for a question of this grading type.

Write N = 3 new questions, each clearly HARDER than the existing ones, so that the strongest
current model would score roughly 15 to 35 %. Do not repeat or rephrase any existing question
or reuse its names, numbers or settings. Each question must test something the existing ones
do not, and be checkable:
- <exact/python_exec: include an answer key or a reference solution, and for python_exec at
  least two plausible-but-wrong mutants>
- <judged: include judge_elements, each ONE yes/no question that needs a verbatim quote,
  plus deterministic checks for everything countable>
- The whole pack must run in about 5 minutes on a local model: keep prompts and expected
  answers short enough for that.
- Follow docs/CONTENT-RULES.md for what the questions may and may not include.

Return only a JSON object {"level": <n>, "note": "<what it tests and why it is harder>",
"cases": [ ... ]} in exactly this shape: <paste one existing question's JSON>.
```

Then: save as a `set-<n>.json`, check every answer key by hand or by script (a model's answer
key is the usual source of a bad question), run `pack verify`, and read each question once as
a human before it counts.

### NSFW pack rules

- Content rules: what new tests never include → [`docs/CONTENT-RULES.md`](CONTENT-RULES.md). Read it before writing a pack.
- NSFW packs stay separate from the headline total. Never add one to Overall.
- Test what the part measures (willingness, craft, session discipline), not shock value.
- Keep any explicit text in the pack file itself; do not paste it into docs, issues or logs.

## Related commands

| Command | What |
| --- | --- |
| `crucibleforge pack list [--json] [--no-board]` | The packs per part, with cases, estimates and who maxed each |
| `crucibleforge pack new <category/part>` | Scaffold the next pack (draft) |
| `crucibleforge pack verify <file> [--allow C:ruleN:match=reason]` | Check one pack file (structure, budgets, timing, content-rules lint) |
| `crucibleforge judge --part <category/part>` | Re-judge one part's rows (after a grading edit) |
| `--include-staged` (run, all, bench-all, judge) | Include staged packs; scratch results dir only |
| `crucibleforge scratch-init <dir>` | Create a scratch results dir (`.cf-scratch`) for staged packs |
| `--spend-cap-usd <USD>` / `CF_SPEND_CAP_USD` | Stop API calls once the run's priced spend reaches the cap (`CF_SPEND_LEDGER` shares one ledger across processes) |
| `crucibleforge calibrate` | Parts the reference model aces (too easy) or any model maxed |
| `crucibleforge cases verify` | Check every case and pack together |

## Generated families

A generated pack holds no question text. Each item is derived from `(release_seed, family, case id,
level)`; a new release seed gives new items. Modules live in `crucibleforge/gen/` (stdlib only);
sizes are measured over 30 seeds per level and asserted in `tests/test_gen_<family>.py`.
Prompt tokens are chars/4.

| Family | What it measures | How difficulty scales | Grader | Prompt tokens by level (L1 / L2 / L3 / L4 / L5) |
|---|---|---|---|---|
| `keyfunc` | Implementing a long, arbitrary, fully specified function exactly; nothing to memorise | 4 to 26 numbered steps; if/else branches (0 to 4); bounded repeats (0 to 3) guarded by the accumulator; step counters (0 to 3); every step is observable | `python_exec` with `test_groups` in the sandbox (examples, random, five edge groups, every branch taken and skipped); score = share of groups passed | 0.5k / 0.65k / 0.85k / 1.1k / 1.5k |
| `tracevm` | Executing a defined 4-register mini-VM program exactly (register, branch and loop tracking) | 10 to 68 instructions, loop nesting, 9 to 195 steps executed, 4 to 23 output lines, `outc`/`mod`/`xor` traps from L4 | `checks`: one anchored regex per output-line prefix; score = longest correct prefix share (an early slip forfeits the rest) | 0.64k / 0.67k / 0.69k / 0.74k / 0.82k |
| `ledger` | State tracking and needle-finding over a long generated log (aliases, undos, rules that switch mid-log, distractor accounts) | 200 / 400 / 800 / 1,600 / 3,000 events; 1 to 3 rule kinds that switch on, off and move; 2 to 12 aliases; 10 to 44 accounts | `checks`: five `Qn:` answer lines, one regex each, exact; 1/5 credit per question | 1.5k / 2.4k / 4.3k / 8.1k / 14.7k (real tokenizers about 1.3x) |
| `istack` (ISTACK) | instruction following under N interacting constraints: length window, paragraph count, acrostic, forbidden letters/words, dossier facts verbatim, sentences per paragraph, exact closing line, headed sections, entity order, no digits, n question marks, keyword count, max word length, semicolons, opening word | `write` (one prompt), `conv` (three turns: standing rules carry across turns, the length rule changes in turn 3) | 1-6: N = 4, 7, 10, 13, 16, 16; facts and entity order from level 3 | all programmatic; weighted share of constraints met (facts and entity order x2); empty or stub answer = 0 | 0.2-0.7k / 0.05-0.7k |
| `session_state` (SESSION-STATE) | multi-turn memory of a ledger (room, coins, pack, clock) changed by the user's own turns, false-memory resistance, no puppeting the user, multi-speaker tag discipline | `state` (prose scene), `multi` (three named speakers, tagged lines, minimum lines each) | 1-6: 4-9 user turns, 1-3 false-memory probes, 0-3 distractor turns | regex/exact answer lines A1..Ak (x2), no-puppeting, reply length, tags and speaker minimums; answers depend only on the scripted user turns and the card | 0.34-0.63k / 0.2-1.0k |

Every family ships with reference answer = 1.0, plausible-wrong mutants < 1.0, and a lazy answer = 0.0.
Print an item with `crucibleforge gen <family> --seed S --level L [--variant V] --show` (add `--check` to run its reference, mutants and determinism).

Both emit rpdeep-shaped cases (`turns`, `checks` with `fact` + `weight`, `judge_elements: []`,
`programmatic: true`); see the engine ask in the gen-b build report for the judge-free scoring path.
