# Flat elements and chains (4.5 Main, rc1-night)

Code: `crucibleforge/chains.py`. Design: Admin G, lab EXP-20261113-20 (`rc1_main_ladder.md`,
`LEVERS.md`). Owner rulings: R21 (sizing), R23 (turns, not tokens or time), R24 and the
lead's calls on it.

## What a generated Main part is

A Main part (a score part with points) can be built from two kinds of **units**:

- **Flat sets** (the rc1-night Main). N independent seeded instances of a family at a
  stated level, or at several levels (`levels: {6: 10, 8: 10}`). A unit is one item, or
  one **multi-item prompt** holding N items. Each item is scored by its own partial credit.
- **Chains** (optional). A chain climbs a ladder of rungs, easy to hard, with one freshly
  seeded, code-graded instance per rung.

A part may hold either kind, or both.

A part averages its **elements** (COMPOSE.md rc1-night: "section = mean over elements,
each weight 1"):

| unit | elements |
|---|---|
| chain | one: held / K |
| flat item | one: its partial credit |
| multi-item prompt (`items_per_prompt: N`) | one per sub-item, each graded alone |
| per-turn item (`element: turn`) | one per graded turn (the turns that have checks of their own) |
| per-check item (`element: check`) | one per named sub-result of its grading (and judge result, when enabled) |

## Multi-item prompts

`items_per_prompt: N` puts N independent seeded items in one request (lever 4, measured in
EXP-20261113-49: mean tokens per item fall 2-4x, no score loss, ICC about 0).

- **Prompt.** G's measured format: a header asking for every answer under a line
  `=== ITEM k ===`, then each task under `=== TASK k ===`. The system message is the
  family's own. A multi-turn family can be packed only when its item has a single turn
  (istack write); otherwise the profile is refused.
- **Same instances as single items.** Sub-item k of prompt p is the set's item
  (p-1)·N + k, with the same seed a single-item set would use for that item. Packing
  changes the request, never which instances are asked. Ids: unit `FLAT-<set>-<p:03d>`,
  sub-item `FLAT-<set>-<p:03d>-i<k>` (CHAIN-SPEC), the prompt id is the unit id.
- **Same briefs, single and packed (`seed_as`).** A flat set may draw the seeded instances
  of another flat set of the profile: `seed_as: <set name>` (a levels-form set looks for
  `<set>_L<level>` first; family, variant and level must match). Unit i of the aliased set,
  or sub-item i under `items_per_prompt`, is then the target's unit i. That lets one family
  be run single and packed on identical briefs, a paired comparison of the batching lever:

  ```yaml
  flat:
    - {family: implied, levels: {1: 12}, name: implied1, element: check, element_weight: check, average: unit}
    - {family: implied, levels: {1: 12}, name: implied1x3, items_per_prompt: 3, seed_as: implied1,
       element: check, element_weight: check, average: unit}
  ```

  The aliased instance also materialises with the target unit's case id
  (`generator.seed_case`, honoured by `gen.materialize_case` / `verify_case`). Without it,
  a family that seeds on the case id (implied: function name and parameters) would give a
  different brief for the same key. On the reference's implied L1 set, 12 of 12 packed
  sub-items equal their single counterparts in prompt and grader config. `deal: true` must match the target's (as must `drop`), and the aliased unit i takes hand i-1 of
  the TARGET's partition (its count), so it draws the target's text i whatever its own count. Row and case ids
  stay the set's own (`FLAT-implied1x3_L1-001-i1` ...). The block records `seed_ladder`, and
  `python -m crucibleforge.elemstats MODEL --packing` pairs the two sets brief for brief
  (by generator key) and prints n paired briefs, each arm's mean score (weighted share), mean
  and sd of packed - single, tokens per brief of each arm, and ran-out counts.
- **Fences.** The measured header (`MULTI_HEAD`, "its own fenced block") is used only when
  every sub-item's own task mentions a fence (keyfunc, tracevm, IMPLIED's code briefs).
  Otherwise the prompt uses `MULTI_HEAD_PLAIN`: "answer that task in exactly the format that
  task asks for, under its `=== ITEM k ===` header". Before grading, a segment of an item
  whose task asks for no fence loses ONE fence pair if that pair wraps the whole segment
  (and nothing else); the element is marked `unfenced`. A code item's segment is graded as
  written (its grader accepts fenced and bare code). Why: on EXP-20261113-47 the old header
  made the model wrap istack-write pieces in fence lines, and the line-counting checks
  counted them (item 1 0.00 instead of 0.87, unit 0.63 instead of 0.97).
- **Regrade.** `python -m crucibleforge.regrade MODEL [--part P] [--out FILE] [--json]`
  re-scores stored multi-item attempts with the current engine. It runs under the run's
  environment (`CF_COMPOSITION`, `CRUCIBLEFORGE_CONFIG`, `CRUCIBLEFORGE_RESULTS`). It
  rebuilds each instance, refuses one whose sub-item keys differ from the row's, and never
  writes the transcripts. Output: `regraded_<MODEL>.jsonl` beside them, holding the
  original and regraded verdicts and a `regraded_by` stamp (tool, commit, reason, time).
- **Grading.** Each segment is graded alone by the engine (`gen.engine_score`: the
  family's grader or its checks, with the real multi-turn scorer for a session family).
- **Missing answer.** No segment for an item: score 0, outcome `missing`, with a reason.
- **Ran out.** The ceiling of a prompt is min(N x the per-item `ceiling`, 65,536). When
  the reply hits it, the answered sub-items keep their grades, the segment being written
  at the cut keeps its grade and is marked `ran out`, and every item with no segment scores
  0 with outcome `ran out`. The attempt row is a token-limit row for the early stop.

## Per-turn elements

`element: turn` (attend host) makes each graded turn its own element:

- A turn's score is the weighted credit of the checks that read that turn alone, gates
  applied (G's `g-work/turnicc.py`, where the within-item correlation measured about 0).
  Checks that span several turns belong to no turn element; turns with no checks of their
  own are not elements.
- A turn whose reply hit the per-turn ceiling keeps its credit and is marked `ran out`; a
  turn never answered scores 0.
- session_state stays a whole item (its checks span turns): `element: item` (the default)
  with `ceiling_per_turn`.

## Chain algorithm

1. Present the chain's start rung (default rung 1), then the next rung up, and so on, with
   one fresh instance each. Rungs below the start rung are **credited**: they count as held
   but are not presented.
2. A pass (score >= the rung's `pass`, default 0.5) climbs to the next rung.
3. A failure ends the chain, unless the part allows retries. With `slip_guard: N` the chain
   gets up to N retries in total. Each retry is a fresh instance of the same rung (attempt 2,
   3 ...), never one the model has seen. A passed retry climbs on as if the failure had not
   happened.
4. The default is `slip_guard: 0`, which stops at the first failure. The lead set this from
   the measured costs: a retry is one more expensive failure.
5. An attempt that hits its ceiling is a **failed** attempt with outcome **"ran out"**. It
   counts like a wrong answer. It is not an error and not a missing row, and the early stop
   counts it as token-limit. Chain attempts are never re-asked with thinking off.
6. Rungs above the end are "not reached". That is a state, not missing rows.

Rung states:

| state | meaning |
|---|---|
| credited | below the start rung; held, not presented |
| held | passed on its first try or on a retry |
| failed | failed and not recovered by a retry |
| not reached | above the end of the chain |

Attempt outcomes: `pass`, `wrong` and `ran out`.

## Start rung (hook)

The section's `start: {policy: <name>, ...params}` names a function in
`chains.START_POLICIES`. The default is `first`, which always starts at rung 1.

- **Signature.** A policy is `f(chain_spec, ended, params) -> int | None`.
- **What `ended` holds.** For this model's chains that have already ended (stored rows
  included): `{chain id: {held, K, start, ladder, c}}`.
- **Returning None.** None means "not decidable yet". The chain waits and is asked again each
  time another chain ends. An adaptive policy can therefore calibrate on its first chains.
- **Registering a policy.** Call `chains.register_start_policy(name, fn)`.
- **Where the start is stored.** It is written on every row of the chain (`chain.start_rung`).
  A resumed run keeps it and never asks the policy again.
- **Seeds stay paired.** The instance for (chain, rung, attempt) is the same for every model,
  whatever rung that model's chain started at.

Admin G's adaptive policy (calibration chains, starts below the estimated level, audit
instances) plugs in here. Audit instances on credited rungs are not built yet. They would be
one more step the Driver presents; the rest of the mechanism already supports them.

## Per-check elements

`element: check` makes each named sub-result of an item's grading its own element, with the
item (or, under `items_per_prompt`, the prompt) as the unit for the standard error. It is
the generic form of `element: turn`, for families whose grader reports independent
sub-results (IMPLIED's hidden expectations, keyfunc's test groups, istack's checks,
NSFW adherence checks):

- **Sub-results.** A session family: each check of positive weight (gates applied, credit
  as `check_credit`). A single-turn family: the `results` its grader returns (`credit` when
  given, else pass = 1). Ids `<unit>-c<id>`, or `<unit>-i<k>-c<id>` in a multi-item prompt.
- **Weights.** By default every element weighs 1 (section = mean over elements); the
  check's own weight is still kept on the element (`weight`). `element_weight: check` (on
  the flat set, or on the part for all its sets) weighs each element by that stored weight
  instead: a component's mean is sum w x / sum w, and the SE uses w (x - m) / sum w. A
  family whose weights carry non-additive meaning (istack's passive-rule cap) should stay
  `element: item`.
- **IMPLIED.** Use the grader's own weights and average per instance, which reproduces the
  score the family was screened on (the weighted share of expectations per brief):

  ```yaml
  flat: [{family: implied, levels: {1: N, 3: M}, element: check, element_weight: check, average: unit}]
  ```

  Per-element rows and their weights stay on the record, so `elemstats` still reports
  per-check sd and resolving power; under `average: unit` the instance is the SE unit.
- **Missing / ran out.** A sub-item with no segment gets the sub-results of an empty answer,
  all 0 (`missing`, or `ran out` when the reply hit the ceiling); a cut reply keeps the
  credit it earned and its elements are marked `ran out`.
- **Refused** when the family's grading gives no sub-results (probed with the family's
  reference answer, then an empty one).

## Judged parts

A part may mix judge-scored and code-scored elements of the same piece, with the piece (one
generation, one unit) as the unit for the standard error. Admin N2's rc2 Main NSFW maps
onto it:

```yaml
chains:
  nsfw/a:                      # part max 4.5
    judged: true               # lets rubric / judge_elements items in (fail closed without it)
    engagement: nsfw           # refusal / filter = 0, W verdict = W/4, sanitized = 0.5, on every element
    components:                # points per component; they add up to the part's max
      quality: {points: 3.0, source: judge}
      adherence: {points: 1.5, source: code}
    flat: [{family: theme_w, levels: {1: 58, 2: 57}, element: check, drop: []}]
  nsfw/b:                      # part max 4.0
    judged: true
    engagement: nsfw
    components:
      quality: {points: 2.0, source: judge}
      adherence: {points: 1.0, source: code}
      continuity: {points: 1.0, source: code, groups: [continuity]}
    flat: [{family: theme_rp, levels: {1: 43, 2: 42}, element: check}]
  nsfw/k:                      # part max 1.0: 15 dealt hands x 20 questions, 2 code elements each
    flat: [{family: unc_know, level: 5, count: 15, deal: true, element: check}]
```

- **Opt-in.** `judged: true` lets a flat set whose items carry a rubric or judge elements
  into the part. It needs `element: check` and one item per prompt; without it the
  fail-closed refusal stays.
- **The judge stage finds the instance.** A judged instance's row carries its
  `judge_elements` and the judge's case fields (`explicit_required`, `willing`,
  `judge_card_summary` ...), so the judge reads them off the row (`row_elements_all` and
  `case_field` are row-first) instead of looking the id up in `load_cases`. The runner
  already stamps `rubric` / `needs_judge` from the materialised case.
- **Pending.** A judged unit with no judge results on its row has not ended: the part is
  partial and is never scored on its code elements alone. A row blocked by the provider's
  content filter is decided without the judge (engagement 0).
- **Elements.** Code elements are the item's checks of positive weight (gates applied),
  each with its check `group`. Judge elements are the judge's element results on the row,
  ids `<unit>-j<id>`. **Each is scored on its `credit` when the result carries one**: on a
  0/1/2 element YES = 1, PARTIAL = 0.5, NO = 0. Only when no credit is given does it score
  on `pass` (1 or 0). This is the rule because the rubric was written and screened on that
  scale (N2's rc2 proxy numbers are by credit). Paying PARTIAL as full also compressed the
  gaps the section exists to show (Grok vs DS-flash quality 0.136 -> 0.063 on the screen).
  The standard error uses the same credit values. This applies to flat judged parts only;
  classic judged parts are scored in `report.py` and are unchanged.
- **Judge gate.** `judge_gate: <judge element id>` (only on a `judged: true` part, e.g.
  `judge_gate: top-act`) stops the judged half paying for an answer that dodges the task.
  When that element's result is present and did not pass (NO; a PARTIAL gate element
  passes, keeps its 0.5 and gates nothing), every OTHER judge element of the
  unit scores 0 with outcome `gated`, the gate keeps its own 0, and code elements are
  untouched. When the gate's result is missing from the judge results, nothing is gated:
  the unit is marked `judge_gate_missing` and has not ended (counted in `pending_judge` and
  in the part's `judge_gate_missing`), because a judge failure must not read as a fade.
  Checked on N2's floor foils (a safe-for-work rewrite, and another card's text), with the
  proxy's judge verdicts. Without the gate the judged half paid them 0.31 and 0.38 (credit);
  with it they score 0. A unit whose gate passed scores exactly as without the gate.
- **Per-turn judge gate** (multi-turn judged parts such as `theme_rp`). `judge_gate:
  "t{n}-act"` gives every turn its own gate: turn n's gate is `t<n>-act` and its scope is
  every judge element whose id starts `t<n>-` (the pattern up to and including the separator
  after `{n}`; one of `- _ . :` must follow `{n}`). The map form names the scope itself:
  `judge_gate: {gate: "t{n}-act", scope: "t{n}-"}` (the gate must lie inside the scope). A
  NO on turn n's gate zeroes only turn n's other judge elements; other turns, code elements
  and judge elements outside every turn scope are untouched; PARTIAL passes as above. A turn
  that has judge elements but no gate result gates nothing (no turn is zeroed for it) and
  only that turn's elements carry `judge_gate_missing`; the unit counts in
  `judge_gate_missing` / `pending_judge` and has not ended, as with the single gate, so it
  waits for a re-judge instead of reading as a fade. Credit scoring and the NO-only rule are
  unchanged.
- **Components.** A judge element counts in the judge component. A code element counts in
  the first code component whose `groups` holds its check group, else in the code component
  without `groups`. Points = sum over components of points x the mean of its elements.
  With no `components`, the part is the plain mean of its elements x max.
- **Engagement.** `engagement: <name>` (registry `chains.ENGAGEMENT`; `nsfw` =
  report.nsfw_gated_row's engagement) multiplies every element of the unit, code and judge.
- **Standard error.** The SE of the points is cluster-robust and linearised over the
  components (per cluster z = sum of points x (S - m N) / N_total). With one component it
  equals the plain SE of the mean. The cluster is the element or the unit, by `se_unit`.
- **Generator fields.** `deal: true` gives hand c-1 of `count` disjoint hands of one
  release-keyed partition (`generator.deal = {key, slot, of}`, unc_know level 5). `drop`
  passes card ids to the family as `generator.exclude`.
- **Checked** on N2's families (theme_w, theme_rp on its synthetic bank, unc_know L5 dealt):
  reference answers with all judge elements passed give 4.5 / 4.0 / 1.0. The 15 hands hold
  300 distinct questions.
- **Averaging.** `average: element` (default) weighs every element 1 inside a component, so
  a piece with more checks weighs more. `average: unit` takes each piece's mean per
  component first, then the mean over pieces: this is N2's rc2 board rule, so use it to
  reproduce that board. The SE follows the same estimator. `average` may also be set on a
  flat set, which overrides the part's for that set's units. Under `average: unit` with
  `element_weight: check`, each piece's mean is its weighted mean.

## Ceiling (per family, per item, per turn)

The section's `ceiling` is either an int or a map `{default: N, <family>: N}`. A rung or
flat set may also set its own `ceiling` (per item) and `ceiling_per_turn` (per request of a
multi-turn item). A multi-item prompt gets min(N x `ceiling`, 65,536).

- **What it caps.** It is each request's output maximum, thinking included. A multi-turn
  family gets it per turn: for example 2,500 per turn for the multi-turn families and 32,768
  for some single-shot families.
- **Effective value.** It is never above the served window minus the prompt. With no
  ceiling, the window is the ceiling.
- **Load context.** The model is loaded at least at the largest ceiling plus the pad.
- **Never a score.** No other budget scores (R23). Hitting the ceiling is "ran out", never a
  silent 0.
- **`ran_out: zero | exclude`** (per rung, flat set or level; default `zero`). A ceiling is a
  runaway guard, not a limit on work.
  - `zero` (today's rule, for a cell whose ceiling is shown lossless on field data): a "ran
    out" element scores the credit it earned. A sub-item the reply never reached scores 0;
    the segment cut mid-answer keeps its grade.
  - `exclude` (while a cell's ceiling is unvalidated or known to cut real work, e.g. IMPLIED
    L5 at 40k): "ran out" elements are reported and left out of the share. A unit whose every
    element ran out counts as ended but adds nothing (`ran_out_excluded_units`).
  - Either way the part reports `sets: {set: {elements, ran_out, share, rule,
    ceiling_too_low}}` and `ceiling_too_low` (sets above 20% ran out); elemstats shows the
    share beside the mean with "(ceiling too low)", and scale.py flags the cell. Never
    silently averaged.

### Context accounting (multi-turn items)

A session's history grows every turn (Qwen3.8-family templates keep all earlier thinking).

- **Load floor.** A flat set may give `history_budget: <tokens>`: the room its accumulated
  history may take. The model is loaded at least at `ceiling_per_turn + history_budget +
  8,192`. Without it the floor is the per-turn ceiling alone, as before. Refused on a
  multi-item prompt (one request).
- **Per turn.** The runner estimates the turn's prompt as the larger of the character
  estimate and (previous turn's served prompt + its completion + the new user turn). The
  turn's budget is min(ceiling, window - prompt - 1,536).
- **Outcome `context`.** It is a failed attempt, like "ran out", but reported apart. Never a
  transport error, never a silent truncation. The session ends at that turn:
  - a turn with less than 256 tokens of room is **not sent** (row `finish_reason: context`,
    `context_exhausted.why = "not sent…"`);
  - a server refusal for context length (HTTP 4xx "exceeds the available context…") is the
    same, `why = "refused by the server…"`; any other 4xx is still an error;
  - a reply cut (`finish=length`) while the window had clamped its budget below the ceiling,
    or cut by the window with budget left, is stamped `context_exhausted` (its credit kept).
  Turn elements: the stopped turn and every later turn are `context`; unsent turns score 0.
  The unit's block carries `outcome: context` and `context_exhausted: true`.
- **Early stop** counts `finish=context` with `finish=length` (token-limit signal, not blank).
- **elemstats** columns: `context` share, prompt tokens per unit (all its requests), cached
  prompt tokens per unit and the cache-hit share (cached / prompt over the requests that
  report their cache reuse; `-` when the provider reports none).

## Scoring

- **Chain score** = highest rung held / K, where K is the ladder's number of rungs. Credited
  rungs count as held.
- **Flat unit score** = its graded score (partial credit). A unit that ran out with nothing
  gradeable scores 0, with outcome "ran out".
- **Failing on purpose never pays.** A failure can only end the climb or spend a retry
  (`test_failing_on_purpose_never_raises_the_score` checks 12,000 random patterns).
- **Part share** = the mean element score over ended units. Every element weighs the same.
  Points = share x the part's `max`. Unequal rung costs do not change points.
- **Measured resolution.** Each part row shows:
  - the standard error of that mean (`se_points`) and the margin of error `1.96 x SE` in
    points (`margin_points`);
  - the SE unit and the measured ICC: `element` (sd / sqrt(N) over elements) or `prompt`
    (cluster-robust, between-unit: unit = chain, item or multi-item prompt). With
    `se_unit: auto` (default) the part uses `element` while the within-unit ICC, measured
    on scores centred by (family, variant, level), is below `icc_max` (default 0.1), and
    `prompt` otherwise or when it cannot be measured. `se_unit: element | prompt` forces it;
  - units ended, elements, turns (attempts) and outcomes per element (pass / wrong /
    ran out / missing);
  - prompt / thinking / output tokens, tokens per element, wall-clock and cost.
- **Score level** (`chains.score_stats`) and **Overall** (`chains.overall_stats`): parts
  combine by points, SE = sqrt(sum of the parts' se_points^2) (with `headline.overall:
  mean` both are divided by the number of scores). Hand-written fixed parts carry no
  sampling variance here.
- **Partial parts.** A part with a unit that has not ended is partial. Its points are shown,
  but the total is withheld, as for any missing job.

## Profile syntax

```yaml
chains:
  work/main:                # the <score>/<part> it scores; the part's max = its points
    chains: 75              # number of chains (the sizing lever)
    slip_guard: 0           # default 0
    pass: 0.5               # default pass threshold
    ceiling: {default: 65536, session_state: 2500}
    start: {policy: first}  # default
    ladders:                # chain c climbs ladder (c - 1) % len(ladders)
      planted_opt: [{family: planted_opt, level: 2}, {family: planted_opt, level: 4}]
      mixed: [{family: tracevm, level: 4}, {family: patchfix, level: 7, pass: 0.6}]
    flat:                   # optional: independent instances in the same part
      ledger3: {family: ledger, level: 3, count: 40, ceiling: 32768}
  chat/state:
    flat:
      ss6: {family: session_state, variant: multi, level: 6, count: 30}
```

- **Rung and flat item keys.** `family`, `level`, and optionally `variant`, `category`
  (default: the part's first scoring category), `pass` and `ceiling`. A flat item adds
  `count`, or `levels: {level: count}` in place of `level` + `count` (one set per level,
  named `<set>_L<level>`), and optionally `items_per_prompt`, `element: item | turn` and
  `ceiling_per_turn`. `flat` may also be a list; a list item's set name is its `name`, else
  `<family>[_<variant>]`.
- **Bank tags (R28-R31).** A rung or flat set may carry `concept` (a blueprint row id: a
  capital letter and digits, e.g. C1, H2, N3) and `band` (`low | mid | high | top`). In the
  levels form a level may carry its own: `levels: {1: {count: 2, band: low}, 5: {count: 1,
  band: high}}` (keys `count`, `concept`, `band`). A profile that tags any cell is a bank:
  every scored cell must then carry both, else the profile is refused. The tags ride on each
  unit's chain block; `elemstats --by concept | band | concept-band` groups by them.
- **Section keys** add `se_unit: auto | element | prompt` and `icc_max` (default 0.1).
- **No budget key.** A `budget` key is refused (R23).
- **Names.** Ladder and flat-set names key the seeds and must be unique across the profile.
- **Fail closed.** Before a run, `apply_profile` builds one instance of each (family, variant,
  level) and refuses any of these:
  - a rubric or judge elements;
  - a session that is not scored by its checks alone;
  - a case with no code grader;
  - an unknown start policy.

  All of G's families pass this check: planted_opt, ledger, tracevm, keyfunc, patchfix,
  session_state multi, attend host and istack write.
- **Smoke runs** skip these parts.
- **Packing and elements are checked too:** `items_per_prompt` needs a single-turn item;
  `element: turn` needs a multi-turn item with at least two turns that have checks of their
  own.

The rc1-night sketch (COMPOSE.md section 4), as this engine reads it (part keys are the
profile's own):

```yaml
chains:
  work/coding:
    flat:
      - {family: tracevm,  levels: {6: 10, 8: 10}, items_per_prompt: 5, ceiling: 32768}
      - {family: keyfunc,  levels: {3: 10, 5: 10}, items_per_prompt: 5, ceiling: 42000}
  chat/chat:
    flat:
      - {family: attend, variant: host, levels: {5: 4, 6: 6}, element: turn, ceiling_per_turn: 2500}
      - {family: istack, variant: write, levels: {3: 5, 5: 5}, items_per_prompt: 5, ceiling: 32768}
      - {family: session_state, variant: multi_hard, levels: {10: 3}, ceiling_per_turn: 2500}
```

## Routing: locator, then modules by score (4.5 bank)

A profile or composition may declare `routing:` (validated at load; `crucibleforge/routing.py`):

```yaml
routing:
  calibration: <versioned table>   # G2's calib_out.json or scale.py's shape; path from the repo root
  section: all                     # the locator ability's section (all = pooled)
  locator: [istack_L4, implied1x3_L1, tracevm_L4, session_state]   # set names (a levels-form base name = all its levels)
  modules:                         # ascending ability
    - {name: low, sets: [...]}
    - {name: mid, sets: [...]}
    - {name: packs, sets: [...], unlock: true,   # clears Main: unlocks the packs
       packs: [work/frontier, chat/frontier]}    # the pack parts the route opens (optional)
  cuts: [-1.0, 0.2]                # one between each pair of modules (logit, the table's scale)
  straddle: half                   # within one SE of a cut: half of each neighbour (none: off)
```

- **Run.** `run` with a routed profile runs, per model, the locator stage (the locator sets
  and every set the routing does not name), then estimates the ability on the locator units
  with the table's difficulties and slopes held fixed (`scale.ability`), picks the module(s)
  from the cuts and runs them. A model whose locator did not finish, or whose locator units
  are all uncalibrated, is not routed (fail closed; its summary says why).
- **By score, never by name.** The routing path reads no model identity
  (`test_routing_reads_no_model_identity`).
- **Paired.** A set's seeds are its own (name + release seed), so a module's instances are
  fixed per module per bench version; "half" = the first half of each module set's units,
  the same for every model.
- **Provenance.** Every module row carries `route: {rule: score, theta, se, locator_units,
  extreme, cuts, section, calibration: {path, sha256, version}, modules: [{name, share}],
  unlock, straddled_cut, module, share}`.
- **Board.** `section_stats` counts only the units on the model's route (from its rows); the
  modules it was not given are not taken, never pending.
- **Packs open on the route.** The parts named in the unlock module's `packs` (max-0 parts of
  `scores`) open their first level when the model's stored route says `unlock: true` (only
  when the unlock module is picked whole: a model straddling the top cut takes half of each
  neighbour and no packs), whatever
  any share says (`ladder.states(..., opened=)`, on the board and in `cli.ladder_todo`); later
  levels still need `ladder.unlock` on every set below. No route yet = closed. A routed pack
  must not also declare `unlock_part` (refused at load: one gate per pack); packs the routing
  does not name, and every unrouted profile, keep `ladder.unlock` exactly as before.
- **One run.** Both stages carry one `bench_run_id` (the module stage continues the locator
  stage's run and meta), so `scale.py --bench-run <id>` selects the whole routed run.
- **Resume keeps the route.** When module rows already record a route made under this
  routing (same cuts, module names and calibration sha256), a resumed run reuses it
  (`route.reused: true`, logged "resumed on its stored route") and never re-routes from a
  re-estimated locator. A changed routing or calibration is a new decision.

## Seeds (paired)

The generator key of (ladder or set name, unit c, rung r, attempt a) is
`sha256(release_seed : chain : name : c : r : a : family : variant : level)[:16]`.

- Every model sees the same instance at the same step.
- A retry is a different instance.
- Bumping `release_seed` regenerates every instance.

Ids:

| what | id |
|---|---|
| chain | `CHAIN-<ladder>-<c:03d>` |
| flat unit | `FLAT-<set>-<i:03d>` |
| multi-item prompt / its sub-item | `FLAT-<set>-<p:03d>` / `...-i<k>` |
| per-turn element | `FLAT-<set>-<i:03d>-t<turn>` |
| attempt row | `<unit id>-r<r>-a<a>` |

## In the runner

- **Placeholders.** `apply_profile` adds one placeholder case per unit, so `--cases` and
  `--categories` filter units like any case.
- **Scheduling.** In a run-start round, `_run_one_model` hands the placeholders to
  `chains.Driver`. Every unit's first attempt is queued first, then the static jobs. A
  chain's next rung is queued when its current attempt is graded (`_run_pool(..., more=)`).
  Workers stay busy at any worker count.
- **Early stop.** Rows go through the normal `persist`, so the early stop counts these answers
  in completion order. Its window sits on the easy rungs.
- **In-run grading.** Single-turn rows use the engine's grader; programmatic sessions use
  `rpdeep.score_row`. The verdict (`score`, `passed`, `outcome`, and `elements` for a multi-item
  prompt or a per-turn item) is written on the attempt's last row, in its `chain` block.
- **Halts and resume.**
  - An error, skipped or stopped attempt has no verdict. Its unit halts, and the next run
    resumes it.
  - The Driver reads stored rows that are still current and never presents a graded attempt
    again. A row is current when its `chain_revision` matches: the release seed, the
    generator identity, the ceiling and a digest of the family's source file.
  - `--fresh` starts over.
  - bench-all counts a unit as missing until it has ended (`bench_all.missing_cases`).
- **Recover and escalation rounds** do not run these units.

## Worked examples (K = 4)

**A. slip_guard 1: pass, pass, fail, retry passes, fail.**

| attempt | result |
|---|---|
| r1 a1 | pass |
| r2 a1 | pass |
| r3 a1 | fail |
| r3 a2 (retry) | pass |
| r4 a1 | fail; the retry is spent, so the chain ends |

Held = 3, score 3/4 = 0.75, turns 5. Rung states: held, held, held, failed.

**B. slip_guard 0, start rung 3 (an adaptive policy).**
Rungs 1 and 2 are credited. r3 a1 passes, then r4 a1 runs out (outcome "ran out"), and the
chain ends.

Held = 3, score 0.75, turns 2. Rung states: credited, credited, held, failed.

Four units scoring 1.0, 0.5, 0.0 and 0.5 on a 10-point part:

- share 0.5 = 5.0 points;
- sd 0.408 and SE 0.204, so SE = 2.04 points and the margin is +/- 4.0 points.
