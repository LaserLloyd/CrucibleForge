# The time budget check (`crucibleforge budget`)

The 4.5 main test (Core) has to be quick on a home machine, and the budget is literal: a **non-thinking
model at 20 tokens/s on one slot** finishes Core (generation + judge + model load) in **at most 30
minutes**. Every case you add or change costs minutes, so check before you merge:

    uv run crucibleforge budget --home --tps 20        # the whole Core, per-case table, packs
    uv run crucibleforge budget --case MY-NEW-CASE     # just your case(s)
    uv run crucibleforge budget --home --tps 15 --json # what-if / machine output

It reads only committed files, so it runs in CI and on a clean clone (no `results/`, no `models.yaml`).
Exit code 0 = within the limit, 1 = the limit or a case is over, 2 = a configuration problem.
`crucibleforge cases verify --profile bench45` runs the same check as a warning (`--budget error` to
make it fail, `--budget off` to skip).

## What it computes

    core_home_min = nominal_tokens / (tps x slots) / 60  +  judge_calls x judge_s_per_call / 60  +  load_min

from the profile's `budget.home` block (defaults: `tps 20, slots 1, judge_s_per_call 25, load_min 3,
limit_min 30`); `--tps`, `--slots`, `--judge-s` override it. Thinking models are not in the gate: their
real time is the board's `bench_min`.

**Nominal tokens** of a case, best source first:

| Source | Meaning |
|---|---|
| `NOMINAL` | the median over the profile's `budget.reference_models` of their output tokens on this exact case content (`case_hash`); per-turn median, summed |
| `ESTIMATE` | a new case before any pilot: `meta.budget.anchor_tokens`, else the committed target in `budget/targets-45.json` |

Changing a case changes its hash, so old rows stop counting for it: a rewritten case falls back to
`ESTIMATE` until the pilot has run it. A case marked `(pending 4.5 rewrite)` still has its 4.1 text; its
number is the 4.5 design target.

A `NOMINAL` case that is more than **25% over its target** fails the check. If the new cost is deliberate,
put `"retarget_note": "why"` in its entry in `budget/targets-45.json` and raise the target in the same commit.

## Packs

Ladder sets are priced the same way (every rung reached, the worst case) and printed as `packs_min`:
reported, never gated. A generated case with no measurement is sized from its family's
`LEVELS[level]["answer_tokens"]`; one with neither is listed as `unsized` and counts 0.

## Refreshing the measurements

After a pilot: `scripts/build_budget_reference.py --results <copy of results>` (never the results dir of a
running bench), or look at a live directory without committing: `crucibleforge budget --results DIR`
(read only).
