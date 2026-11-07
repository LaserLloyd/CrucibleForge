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
  pending. A pack that has not unlocked is locked and counts nothing.
- **MAXED.** A part is **MAXED** when a model scored at least 90 % on the standard
  questions *and* on every pack that exists, so there is nothing harder left to give it. The
  board marks the part (and the whole category, when all of its parts are maxed) with a **MAX** marker;
  `crucibleforge calibrate` and `pack list` name the models that maxed each part. A part with
  rows excluded by an unparsable verdict is shown as partial, never MAXED. MAXED means: write
  the next pack (see below).

## How a run uses them

No flag is needed. `run`, `all` and `bench-all` run the standard questions first, work out
which packs are due from the model's own board rows, and run those. `--no-ladder` (or
`--no-tiers`) on `all` and `bench-all` skips them. To run one pack's questions by hand, pass
its ids: `uv run crucibleforge run --cases LRZ1-cell-row-trace,LRZ2-tariff-ledger-deduction
--model …`. Pack questions are graded and stamped per question, so editing one re-runs only
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

NSFW packs are separate from the headline total (NSFW never counts in Overall). Every
character in them is an adult with a stated age of 21 or older.

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
- Grading-only edits (checks, `judge_elements`) do not change the suite revision, but rows
  graded before the edit need re-judging.

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
   and prints the time estimate.
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
- All characters in any adult content must be adults (21+); no sexual content involving
  minors, ever. No real people.

Return only a JSON object {"level": <n>, "note": "<what it tests and why it is harder>",
"cases": [ ... ]} in exactly this shape: <paste one existing question's JSON>.
```

Then: save as a `set-<n>.json`, check every answer key by hand or by script (a model's answer
key is the usual source of a bad question), run `pack verify`, and read each question once as
a human before it counts.

### NSFW pack rules

- Every character is an adult with a stated age of 21 or older, and every act is consensual.
  No minors, no incest, no bestiality, no real people.
- NSFW packs stay separate from the headline total. Never add one to Overall.
- Test what the part measures (willingness, craft, session discipline), not shock value.
- Keep any explicit text in the pack file itself; do not paste it into docs, issues or logs.

## Related commands

| Command | What |
| --- | --- |
| `crucibleforge pack list [--json] [--no-board]` | The packs per part, with cases, estimates and who maxed each |
| `crucibleforge pack new <category/part>` | Scaffold the next pack (draft) |
| `crucibleforge pack verify <file>` | Check one pack file |
| `crucibleforge calibrate` | Parts the reference model aces (too easy) or any model maxed |
| `crucibleforge cases verify` | Check every case and pack together |
