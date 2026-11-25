# The judge

## Choosing the judge

Any model can be the judge, local or hosted: `--judge <provider>:<model-id>`, a registry label, or a name under
`judge:` in `models.yaml`. The model under test and the judge are chosen independently
([CONFIG.md](CONFIG.md#mixing-a-local-or-hosted-model-with-a-local-or-hosted-judge)); for example a laptop runs a
local 8B model and OpenRouter judges it. The scores published on MakoBench are judged by the same model run locally
at 8-bit (Q8): the plain Qwen 3.8 27B.

**Quick setup: the judge through OpenRouter.** Set `OPENROUTER_API_KEY`. `models.yaml` from `config --init` already
carries Qwen 3.8 27B through OpenRouter as a judge candidate with its price (`qwen27-judge-openrouter`):

```bash
uv run crucibleforge all --models <label> --judge openrouter:qwen/qwen3.8-27b
```

A hosted judge is billed by its provider (this one lists at about $0.04 in and $1.50 out per million tokens; check
its page). It is not part of the model's Run cost and is not limited by the run's spend cap; its spend is printed
on its own line ("judge cost not priced" when its entry has no price). A hosted judge receives every reply it judges, NSFW+ included; pass `--no-nsfw-plus` to leave NSFW+ out.

**Your own GPU: a local judge.** Load the model (Qwen 3.8 27B at 8-bit is about 29 GB of weights plus a 16,384-token
context) in LM Studio or on a StudioForge server and point the run at it:

```yaml
providers:
  lmstudio:
    type: lmstudio                 # LM Studio; llama-server: type: openai, http://127.0.0.1:8080/v1 (a local address is a local judge)
    base_url: http://localhost:1234/v1
```

```bash
uv run crucibleforge all --models <label> --judge lmstudio:<model key>
```

**Your board and publishing it.** Rows judged by another judge are scored and ranked on your own board, marked ⚖
beside Overall with "judged by <id> (<provider>); MakoBench's published scores use the local Qwen 3.8 27B Q8 judge";
a board that mixes judges says so in one line above the table. `report --public` refuses such rows unless you pass
`--allow-other-judge`. Re-judging a model with another judge makes the board read only the latest judge for that
model.

**No judge.** `--no-judge` scores everything code checks; the judged parts read `pending`, Overall stays blank, and
`crucibleforge judge --models <label>` fills them later.

## Registry `judge:` block

A `models.yaml` entry may carry a `judge:` block. Its keys override the entry for
its use as a judge only, for example thinking on and the sampler a hosted judge requires:

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
