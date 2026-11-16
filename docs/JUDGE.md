# Judge configuration (4.5 additions)

Everything here is optional. Without these keys the judge behaves exactly as in 4.1.

## Registry `judge:` block (a hosted model as the judge)

A `models.yaml` entry may carry a `judge:` block. Its keys override the entry for
its use as a judge only:

```yaml
- name: grok-4.7
  provider: xai
  model_id: grok-4.7
  judge:
    thinking: true        # thinking ON; the strict JSON schema is dropped, parse_verdict reads the reply
    context: 16384        # alias of context_length; the judge default (16384) when absent
- name: kimi-k3.1-pro
  provider: moonshot
  model_id: kimi-k3.1-pro
  judge:
    thinking: true
    context: 16384
    temperature: 1        # Kimi accepts only temperature 1 and top_p 0.95
    top_p: 0.95
    sample_temperature: 1 # extra samples are drawn at this temperature (default 0.7)
```

Allowed keys: `context` / `context_length`, `thinking`, `temperature`, `top_p`,
`sample_temperature`, `max_tokens`, `extra_body`, `no_schema`, `row_timeout_s`.
Keep the context the same for every judge you compare: the judge prompt is
clamped to fit it, so a different context changes the input.

## Sampling by element class (`judge_policy`)

```yaml
judge_policy:
  element_class: {fact: 1, craft: 3}
  sequential_majority: true
```

- Every judge element has a class: `fact` or `craft`. Write `class: craft` on the
  element to set it. Without the field: an rpdeep element (it has `turns`) is a
  fact; an element whose `dim` is a 0-10 dimension of its rubric is craft;
  every other element (a yes/no fact check, a flag) is a fact.
- A call that judges several elements together draws the largest count among
  their classes. A class the table does not list draws 1.
- With `element_class` set, the table replaces the 4.1 tag rules (`private`,
  `binary`, `willing`) for rows that carry elements. `samples3_rows` /
  `samples1_rows` and willingness rows (W 0-4) keep their old counts. A row
  without elements keeps `craft_samples` (default 1).
- rpdeep rows: each fact group draws the samples of its own elements' classes
  (the `CRUCIBLEFORGE_RPDEEP_SAMPLES` knob no longer decides).
- `sequential_majority`: a 3-sample call draws 2 first and the 3rd only on a
  split (any element's verified vote differs, any flag differs, or a dimension
  differs by 2+ points). The verdict records `samples_used`, `votes` (per element,
  verified yes/no per sample) and `sequential: true`. The stored `samples` stays
  the policy count (3), so a later run does not treat the verdict as outdated.
- An explicit `--samples` still overrides the count for every row.
- The row pool is sized from the calls a row holds in flight (2 for a sequential
  row), not from the rounded mean of the sample counts.

## Other keys

- Profile `row_timeout_s` (4.5: 1200): the per-row ceiling of one judge verdict.
  Order: candidate / `judge:` block > profile > `judge.row_timeout_s` in
  models.yaml > 600 s default.
- The judge reads the profile named by the run (`--profile`), else `bench`.
