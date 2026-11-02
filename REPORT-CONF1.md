# CONF-1 — case lint: one requirement per judge element (Part 1)

Worktree: the CONF-1 job worktree · branch `conf-lint` · base commit `92bfc47`

## What changed

**`crucibleforge/verify_cases.py`** — the lint itself.

- `requirement_joins(q)` — counts the REQUIREMENT joins in a judge element's
  question. A join is a top-level conjunction that opens a NEW clause
  (`AND does she come`, `while he says her name`); a conjunction that
  coordinates words inside one requirement (`hands and knees`, `her tongue,
  what she does with her hand`) does not. Parenthetical asides and
  double-quoted spans are masked out first — they elaborate one requirement,
  they never add one.
- `_mask_asides(q)` — blanks asides/quotes, keeping offsets so a join can be
  reported as the conjunction plus the word that follows it.
- `_check_judge_elements` — an element whose `dim` is in `judge.GATING_DIMS`
  and whose question joins more than two requirements is now an ERROR. The
  message names the element, the joins it found and the dimension that would
  be capped.

**`crucibleforge/judge.py`** — `GATING_DIMS`, the set of dims whose element
failure gates a score (`explicitness`, `constraints`, `continuity`,
`identity`, `integrity`, `character`, `voice`, `obeyed`). It is the set of
every dim the shipped case set actually uses on a judge element, and a test
fails if a case ever uses a dim outside it — so extending the case set
means extending this list on purpose.

**`tests/test_case_lint_compound_20261102.py`** — 14 tests. The pre-fix NC3
text (the `t6-peak-as-named` element 43c7483 removed, verbatim) is kept as
the regression fixture and must fail the lint; the shipped split element
must pass it.

**Docs** — README (`Adding cases`), `docs/CUSTOM-TESTS.md` (custom tests),
`CHANGELOG.md` (4.0.3).

## How the rule was tuned on the corpus

The plan asked for the pre-fix NC3 text to fail and every current case to
pass. A plain count of every `AND`/`while`/`, and` in a question cannot do
that: it flags 17 shipped elements, most of them false positives where the
conjunction coordinates words inside one requirement (`hands and knees`,
`clothing and the lit lamp`, `her tongue, what she does with her hand`).

The rule that separates them is whether the conjunction OPENS A CLAUSE:

| text | conjunction | join? |
|---|---|---|
| `..., AND does she come ...` | clause follows | yes |
| `... while saying his name?` | clause follows | yes |
| `... on hands and knees ...` | noun phrase follows | no |
| `... (she comes, climaxes, clenches around him) ...` | masked aside | no |
| `... "the tide's breath" ...` | masked quote | no |

Measured over the corpus (463 judge elements, 393 cases): the maximum
requirement-join count over all dim-bearing elements is 1, so nothing in the
shipped set trips the lint, while the pre-fix NC3 text has 2 (`AND does`,
`while saying`).

## Verification

- `cases verify` on the full case set: **393 cases, 0 judge-element
  errors**. Ladder pack questions go through the same checker: 27 inline
  cases, 0 errors.
- The lint is reachable from the CLI: `verify_all` returns 1 for a case
  carrying the pre-fix element and 0 for the split one (test
  `test_the_lint_runs_in_cases_verify`).
- `ruff check` clean on all three touched Python files.
- No case file was touched, so the suite hash (`e3d1c6fb`) and every stored
  row are unchanged; `judge_elements` are grading keys, excluded from the
  suite hash anyway.

## Final pytest summary line

Full suite, with the command from the job rules
(`uv run --offline pytest -q -n 4 -o addopts="" --basetemp=<worktree>/../tmp-conf1`):

```
1820 passed, 9 skipped, 365 warnings in 1563.78s (0:26:03)
```

The new file alone (`tests/test_case_lint_compound_20261102.py`): `14 passed in 0.89s`.
The full summary line is also in `FINAL-PYTEST.txt`.

`crucibleforge cases verify` on the whole case set:
`393 cases checked, 0 with problems (87 reference solutions executed, 57 answers re-derived)`.

No FAILED/ERROR lines in the run. `ruff check` clean on the three touched
Python files.

## Commits

- `c8cdc12` cases verify: reject a compound judge element on a gating dim
- `b7b57c9` docs: the one-requirement-per-judge-element rule for case authors

Plain messages, no trailers, no URLs.

## Open issues / notes for the maintainer

1. **The threshold is "more than two requirements", not "more than one
   join".** Two requirements in one element (`is the sex explicit AND does
   she come`) is allowed — that is exactly the shape NC1 and NC2 ship and
   the shape 43c7483 split NC3 down TO. The lint only rejects the third
   requirement onward. If the intent is stricter (one requirement per
   element, full stop), change the comparison in
   `_check_judge_elements` from `len(joins) > 1` to `len(joins) > 0` — but
   that would reject 21 shipped elements today and needs case edits first.

2. **`GATING_DIMS` is a hand-maintained list, deliberately.** It currently
   equals the set of dims the shipped case set uses. A new case that puts a
   `dim` outside the list is caught by
   `test_every_dim_used_by_a_judge_element_is_gated` and must be added to
   the list — the alternative (deriving it from `RUBRICS`) would silently
   widen the lint to every rubric dim, including ones no case uses.

3. **The join detection is lexical, not a parser.** It asks "does the word
   after this conjunction open a clause?" using a fixed list of auxiliaries,
   modals, copulas, subject pronouns and interrogatives. A conjunction
   followed by a rare clause-opener (a verb like "wrote", "came") is not
   counted. No shipped element is affected; a future one might be, and the
   failure mode is a missed lint, not a false ERROR.

4. **Only `judge_elements` are linted.** The deterministic `checks` grader
   has the same "one element, one requirement" idea but no `dim` and no
   cap, so a compound check there is not a scoring defect. Out of scope for
   this part.

5. **`cases verify` is slow in this environment** (~26 min for the full
   pytest suite, ~15 min for `cases verify` on its own) because both
   execute the code-bank reference solutions in the real sandbox. The
   judge-element lint itself runs in about a second over the whole case
   set, so iterating on the lint does not need the full run.

6. **No version bump.** `SUITE_VERSION` stays `4.0.1`: no case content, no
   grader, no metric and no prompt changed, so nothing on the board goes
   stale. The CHANGELOG carries the change under 4.0.3 — if the release
   train prefers folding it into 4.0.2, that is a one-line edit.
