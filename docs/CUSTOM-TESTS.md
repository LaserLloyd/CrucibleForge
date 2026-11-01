# Custom tests and ladder packs

CrucibleForge's board has three built-in categories, each out of **10 points**:

| Category | Sub-scores (max points) | In Overall? |
|---|---|---|
| Coding | Functions 5 · Predict & bug hunt 2.5 · Tool use 2.5 (part keys `code` · `reasoning` · `agentic`) | yes |
| Chat | Deep RP 3 · Conversation 3 · Character 2 · Creative writing 2 | yes |
| NSFW | Uncensored 2 · NSFW RP 4 · NSFW writing 4 | no, shown beside it |

**Overall = Coding + Chat** (max 20 on the standard test). A sub-score's points are its
share of the standard questions × its maximum; a category is the sum of its sub-scores. All of
this is configuration in `profiles/bench.yaml` (`scores:`, `headline:`, `ladder:`), not code.

Two ways to extend it, both without touching code:

1. **Custom tests**: your own categories, scored and totalled as you choose.
2. **Ladder packs**: harder questions for an existing sub-score, so models that max the
   standard test can keep climbing.

---

## 1. Custom tests

Put your cases in `cases/private/<category>.json` (git-ignored) and declare the test in the
git-ignored overlay `profiles/bench.private.yaml`:

```yaml
private: [my-cat]                      # categories that never leave this machine
cases:
  my-cat: [MC1-first, MC2-second, MC3-third]
repeats: {my-cat: 1}
custom:
  my_test:
    label: My test
    include_in: none                   # overall | none | a category key (work, chat, nsfw)
    public: false                      # default false: scrubbed from `report --public`
    parts:                             # at most 4 sub-scores, whole-number maxima
      recall:  {label: Recall,  max: 6, scoring: [my-cat], cases: [MC1-first, MC2-second]}
      tone:    {label: Tone,    max: 4, scoring: [my-cat], cases: [MC3-third]}
```

- `scoring:` lists the components a sub-score is made of. A list weighs each component by its
  number of cases in that sub-score; a mapping (`{my-cat: 10, steer: 5}`) gives explicit weights.
- A case's grading is whatever its case object says: a deterministic `grader`, a judge `rubric`
  with `judge_elements` (YES/NO with a quote), deterministic `checks`, or both.
- `cases:` limits which rows feed the sub-score (components are per category, so this is how
  one category is split over several sub-scores).
- `include_in: overall` adds the test's points to Overall; `include_in: work` (or `chat`,
  `nsfw`) adds them to that category; `none` shows it as its own column only.
- `public: false` (the default) keeps every name, case id and number of the test out of
  `crucibleforge report --public`; the public build fails closed if anything leaks.

A private category with **no** `custom:` declaration is a custom test on its own: one
sub-score worth 10, `include_in: none`, private. That is how an existing private test whose
overlay predates `custom:` (its weight under `scoring.chat`) is wired: the overlay stays as it is,
the test is its own column, and it never counts in NSFW, Chat or Overall.

---

## 2. Ladder packs (harder questions)

Every sub-score can grow a ladder of **packs**:

```
cases/ladder/<category>/<sub-score>/set-<n>.json            (in the repo)
cases/private/ladder/<category>/<sub-score>/set-<n>.json    (local only)
```

- A pack is worth **1 point in total**, whatever its size or difficulty: with 3 questions each
  counts ⅓ (2 of 3 solved = +0.67), with partial credit inside a question where its grader gives it.
- A pack has a `level`. The packs of a level are given to a model only after it scored
  **≥ 90 %** (`ladder.unlock`) on the standard questions of that sub-score **and** on every pack
  of every lower level. Packs of one level run together; levels need not step up evenly. Put an
  easier pack on the SAME level as the hard one beside it, never below it: a lower level gates
  every level above it, so an easy leftover would hold back the hard pack.
- Packs come in sets of **3** questions. A smaller pack needs `"allow_small": true` and an
  `"allow_small_reason"` saying why (`pack verify` / `cases verify` refuse it otherwise).
- A category passes 10, and Overall 20, only through packs. The board shows "(set n)" for the
  highest level scored, and **MAXED** when a model is ≥ 90 % on everything that exists for a
  sub-score (every sub-score of a category: the category is MAXED) — time for a new pack.
- Adding a pack never makes existing results stale: every row carries the content stamp of its
  own question (`version.case_hash`), so adding or editing one question re-runs only that
  question's rows. Editing only how a question is judged (its `judge_elements`, card summary,
  willingness switch or metric tags) re-judges the stored answers; editing its `checks` or a
  tool step's `answer_contains` / `answer_forbid` applies at the next report.

### Make one

```
$ crucibleforge pack new work/code
wrote cases/ladder/work/code/set-1.json (draft, 3 question stub(s), 1 point in total)
```

Options: `--count N` (any number ≥ 1), `--level L` (default: one above the highest existing),
`--grading exact|python_exec|judged`, `--category <case category>`. The file is a **draft**
(`"draft": true`): runs ignore it until you clear the flag.

A pack file either names existing case ids or carries its questions inline. Worked example —
three program questions (one shown in full):

```json
{
  "level": 1,
  "note": "Programs a model needs real algorithmic insight for; 3.9 standard CZ05 is maxed.",
  "budget": 65536,
  "context": 73728,
  "cases": [
    "CZ11-lex-triple-substring",
    "CZ08-triple-substring",
    {
      "id": "CODE-P1-Q3-KTH-PAIR-SUM",
      "category": "coding",
      "difficulty": "hard",
      "max_tokens": 6144,
      "prompt": "Implement `def kth_pair_sum(a, b, k):` ... (full spec)",
      "grader": "python_exec",
      "grader_config": {"tests": "assert kth_pair_sum([1, 2], [3], 1) == 4\n..."},
      "reference": "def kth_pair_sum(a, b, k):\n    ...",
      "mutants": ["def kth_pair_sum(a, b, k):\n    return sorted(x + y for x in a for y in b)[k]  # off by one"]
    }
  ]
}
```

`budget` (the rows' output budget) and `context` (the context the model is loaded at) are
optional and apply to the pack's rows only.

### Check it

```
$ crucibleforge pack verify cases/ladder/work/code/set-1.json
note: 3 question(s), 1 point in total (0.33 each)
ok
```

`pack verify` refuses a pack that has no question, fewer than 3 questions without
`allow_small` + `allow_small_reason`, a `points` other than 1, a non-positive
`level`, an unknown or duplicate case id, or a scaffold id left as `...-CHANGEME`; it runs each
inline question through the case checks (required fields; a `python_exec` reference passes its
own tests in the sandbox and **every listed mutant fails them**; a `repo_tests` task's mutants
bite; `verify` blocks re-derive one-answer golds) and, for a judged question, builds the judge's
input with a full-length answer and refuses it when it would not fit the judge's 16k context.
`crucibleforge cases verify` also checks every ladder file.

### When to add one

```
$ crucibleforge calibrate
Calibration — reference model minimax-m3
sub-score                           max ref share  verdict
Coding / Functions                    5      100%  TOO EASY (≥ 90%): rethink difficulty / add examples; MAXED by minimax-m3: needs a new pack
...
```

A sub-score the reference model (`calibration.reference`, default `minimax-m3`) scores ≥ 90 %
on is too easy for the standard test; a sub-score any model has MAXED needs a new pack.
