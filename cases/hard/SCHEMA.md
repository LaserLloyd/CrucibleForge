# Hard sets (4.5): file format

Each score (Coding, Chat, NSFW) is /10 = the standard parts (8) + a **hard** part (2).
The hard cases are authored here and are in the public tree, NSFW included. No scored
case may live under `cases/private/` or in a `*.local.*` path; `cases verify` fails on it.

## Layout

    cases/hard/<score>/<rev>.json     scored set for one revision; score = code | chat | nsfw
    cases/hard/<score>/shelf/         candidate bank: uncertified, never loaded, never scored
    profiles/bench45.yaml             scores.<work|chat|nsfw>.hard: {max: 2, file: cases/hard/<score>/4.5.json}

## Revision file

    {"schema": 1, "score": "code", "rev": "4.5", "cases": [ <case>, ... ]}

`score` must equal the directory, `rev` the file name without `.json`.

## Case object

Identical to a standard case of that score's kinds (same keys as `cases/<category>.json`;
the graders, `judge_elements`, `checks`, `tool_script`, etc. are the same), plus:

- `category`: the kind. code: `coding | codebank | codeagent | tooluse`; chat: `rpdeep | rp | story | steer`; nsfw: `nsfw`.
- `meta` (required):
  - `authorship`: who wrote it (`grok`, `deepseek`, `minimax`, `local`, `human`; Claude writes specs and keys, never explicit prose),
  - `budget.anchor_tokens`: the design target for the anchor's output (a positive number; WP-07 uses it as the ESTIMATE source),
  - `pack`: the authoring/rotation unit (a short id),
  - `certified`: `true` for a scored file (`false` belongs in `shelf/`).

`id` must be unique across the profile's standard cases and every hard file.

## Promotion and `case_hash`

`meta` and `category` are left out of `case_hash`. Moving a case from hard to standard
(drop `meta` and `category`, add it to `cases/<category>.json`) keeps its hash, so rows
already run stay current. The hard files are not part of the whole-suite `cases_hash`.

## Checks

`uv run crucibleforge cases verify --profile bench45` checks the schema, unique ids,
the meta, the private-path rule, that `max` + the standard parts add up to 10, and runs
each case through the same checks as a standard case.
