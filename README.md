# CrucibleForge — an LLM capability benchmark with a real hard tier

CrucibleForge ranks language models on the things that decide whether a model can
actually do a job: **hard reasoning and math with verifiable answers, coding
graded by execution, tool use, instruction following, long-context retrieval,
system-prompt steerability, safety calibration, roleplay / creative quality,
and speed** — against **any OpenAI-compatible endpoint**: local (LM Studio,
Ollama, llama.cpp / llama-server, vLLM, StudioForge) or hosted (DeepSeek,
OpenRouter, OpenAI, Groq, Together, Mistral, Open WebUI …).

* **Hard tier that doesn't ceiling.** 251 cases, **158 of them hard**
  (`crucibleforge cases list` counts them): competition-style math
  (integer answers, every one re-derived by a brute-force `verify` expression),
  logic/state-tracking puzzles with unique solutions, coding problems whose
  tests **enforce the right complexity** (an O(n²) inversion count times out;
  matrix exponentiation is required for n = 10¹⁸), decoy/injection/enum
  tool-use traps, 12k–24k-token long-context retrieval, multi-constraint
  instruction following. Strong open 27–31B models that swept the old tier
  score well under 100% here.
* **Hard question, trivial evaluation.** Objective cases grade
  deterministically (numeric / exact / contains / code execution / tool-call
  shape). Cases whose answer is prose use the `reference` grader: the model
  is shown a gold answer and a small judge only decides *equivalent or not* —
  any instruct model can do that, so the judge is not the bottleneck.
* **Judge local or remote.** Creative/safety categories use an LLM judge with
  structured output, a 5-probe calibration canary, injection fencing,
  self-consistency sampling and position-swapped pairwise Elo. The judge is
  just another registry entry — an uncensored local model, or
  `deepseek-v4-flash` over the API.
* **Providers, not backends.** `models.yaml` declares providers
  (`openai` / `lmstudio` / `studioforge`) and models that reference them.
  Remote APIs run cases **concurrently**; token **cost** is reported for
  priced models. API keys live in environment variables only.
* **Web GUI** (`crucibleforge gui`): pick models/categories/tiers, watch the log,
  read the report, drill into every failed row (prompt, response, reasoning,
  judge note), manage providers/models/judge, discover model ids, test
  connections. Stdlib server + vanilla JS, no CDN, no build step.
* **Suite revision stamp** on every row (`3.0.0+<hash of the case files>`),
  plus a separate **judge fingerprint** and the identity of the judge that
  actually scored each row: the report flags results from a different test set
  or a different judge instead of quietly ranking them side by side.

## The 1-hour `standard` profile and the scorecard

`crucibleforge all --profile standard --models <label> --yes` runs a fixed 56-case
selection sized for **a ~70 tok/s 27B in under an hour including judging**:
perf ×2, RP 6, NSFW ladder 5, steer 3, **10 brutal coding cases** (chosen so
DeepSeek v4-flash scores ~20% — headroom for future models), 10 hard tool
cases, 8 hard instruct, 4 hard reasoning + 6 hard math (pooled as "Reason" in
the scorecard); 1 repeat, per-category budgets with a 16k thinking cap, and a fast non-thinking judge (a 31B uncensored gemma with
`enable_thinking: false`). `profiles/standard.yaml` is plain YAML — copy it to
`<config dir>/profiles/mine.yaml` to make your own; profiles only *select*
from the case files, so per-case results stay comparable.

The report opens with a **Scorecard** — every model gets 0–100 scores:

| | weights (default, `scoring:` in models.yaml overrides) |
|---|---|
| **Chat** | RP 20 · NSFW 20 · Explicit peak 5 · Willing 5 · Steer 5 |
| **Code** | Code 20 · Tools 10 · Instruct 10 · Reason 5 |
| **Total** | both halves combined by weight |
| **T/S** | median gen tok/s scaled so `tok_per_s_full_marks` (100) = 100 — shown beside Total, never folded in (speed is host-specific) |

Components a run did not measure are dropped and the remaining weights
renormalised (the report says which).

## Installing

**The supported install is a clone plus `uv sync`** — run CrucibleForge from
the checkout. A wheel/PyPI install is *not* supported yet: the case set
(`cases/`), the profiles (`profiles/`) and `models.example.yaml` live outside
the Python package and are not packaged into a distribution, and the default
results directory is resolved relative to the checkout. Building and installing
a wheel therefore gives you a CLI with no cases to run; `config --init` says so
rather than raising. Packaging those data files properly is a planned change.

## Quick start

```bash
git clone <this repo> crucibleforge && cd crucibleforge
uv sync                                  # or: pip install -e .
uv run crucibleforge config --init       # writes models.yaml (git-ignored); then edit it
export DEEPSEEK_API_KEY=...              # only if you use a hosted provider

uv run crucibleforge status                   # providers up? judge? case counts
uv run crucibleforge gui                      # http://127.0.0.1:8777
uv run crucibleforge all --profile standard --models local-gemma-e4b --yes   # the 1-hour scorecard run
uv run crucibleforge all --smoke --models local-gemma-e4b --yes     # ~10 min end-to-end
uv run crucibleforge run --models deepseek-flash --difficulty hard  # the hard tier only
uv run crucibleforge judge && uv run crucibleforge report
uv run crucibleforge recover --models a,b --yes     # re-run reasoning-overflow rows, then judge again
uv run crucibleforge pairwise --models a,b --categories rp,nsfw --yes  # position-swapped head-to-head
uv run crucibleforge models discover <provider>     # list what a server is serving
uv run crucibleforge cases verify                   # re-derive every gold answer offline
uv run crucibleforge status                         # providers, residents + leases on the rig, judge, cases
```

`crucibleforge all` = `run` → `judge` → `report`. Results land in
`results/` next to your `models.yaml`: `report.md` / `report.json`, append-only
`runs.csv`, and per-model `transcripts_<label>.jsonl` (every prompt, response,
reasoning, metric and judge verdict). Runs **accumulate** by default; `--fresh`
archives prior transcripts first.

### Registry (`models.yaml`)

```yaml
providers:
  lmstudio:    {type: lmstudio, base_url: http://localhost:1234/v1, api_key: lm-studio}
  ollama:      {type: openai,   base_url: http://localhost:11434/v1, api_key: ollama}
  deepseek:    {type: openai,   base_url: https://api.deepseek.com/v1,
                api_key_env: DEEPSEEK_API_KEY, concurrency: 4}
  openrouter:  {type: openai,   base_url: https://openrouter.ai/api/v1,
                api_key_env: OPENROUTER_API_KEY, concurrency: 4}
judge:
  candidates:
    - {provider: lmstudio, model_id: my-uncensored-judge, context_length: 16384}
    - {provider: deepseek, model_id: deepseek-v4-flash}
models:
  - {name: deepseek-flash, provider: deepseek, model_id: deepseek-v4-flash,
     context_length: 131072, price: {input: 0.14, output: 0.28}}
  - {name: local-qwen, provider: ollama, model_id: qwen3:8b, context_length: 32768}
```

* `type: openai` — any `/chat/completions` server; nothing is loaded/unloaded.
* `type: lmstudio` — managed through the `lms` CLI: one model at a time,
  measured load time, snapshot/restore of what was loaded before, and the
  **wrong-model guard** (LM Studio answers 200 from whatever is loaded, even
  for a bogus id — every reply is verified against the requested model).
* `type: studioforge` — a llama.cpp multi-model server. Loads go through the
  server's **`POST /api/models/<id>/load-recommended`** (exact context per
  slot, server picks placement/KV/slot count; a structured 507 falls back to
  the best fitting `GET …/profiles` entry; older servers use plain JIT) and the run
  then issues that many requests at once (`concurrency: auto`; set a number
  to cap it, `recommended_load: false` for plain JIT). Measured: 70 → 217
  tok/s aggregate on a 27B. Perf cases still run serially so single-stream
  TTFT/tok/s stay honest.
* `extra_body` (per model or provider) merges arbitrary request fields —
  e.g. `{reasoning_format: deepseek}` for llama.cpp thinking models so CoT
  stays out of `content`.
* Config search order: `--config`, `$CRUCIBLEFORGE_CONFIG`, `./models.yaml`,
  `<repo>/models.yaml`, `~/.config/crucibleforge/models.yaml`. A v2 registry
  (`endpoint:`/`rig:`) is upgraded in memory; `crucibleforge config --upgrade`
  rewrites it.
* **OpenClaw users:** `crucibleforge import-openclaw --write` pulls providers,
  models, prices and `${ENV}` key references straight from
  `~/.openclaw/openclaw.json` (remote providers by default,
  `--include-local` for loopback ones). Literal keys are never copied.

## What it measures

| Category | Grading | Hard tier |
|---|---|---|
| **math** | numeric (`Answer: N`), every gold answer re-derived by a `verify` expression | number theory, combinatorics, probability as m+n, digit sums of huge powers, recurrences, Josephus, lattice geometry, dual-base palindromes |
| **reasoning** | numeric / exact / contains / `reference` (gold-answer judge) | BBH-style logic, unique-solution logic grids, knights & knaves, state tracking, ciphers, letter counting, weekday arithmetic, BFS puzzles, bug-finding & "why does this query double-count" |
| **coding** | real execution in a `bwrap` sandbox, pass@1, stdout sentinel | three hard sets: CX (LRU-with-peek, exact-fraction expression parser, glob, inversions 200k, recurrence n=10¹⁸, distinct substrings, sudoku, convex hull, word ladder, sweep-line, stack VM, roman, rate limiter, LCS-by-property), CY (repeat-≥k substring, regex NFA with pathological input, cron next-fire, lazy segment tree, Hungarian assignment, max-flow, 2-SAT, bitset knapsack, sliding median, offline distinct-count), CZ (π(10¹⁰), 200k segment crossings, TCO Lisp interpreter, offline dynamic connectivity, Knuth-optimized BST, bit-parallel LCS, NTT convolution, 3-string LCS, range k-th, k disjoint subarrays) — every test enforces the right complexity, every reference solution is executed by the verifier. DeepSeek v4-flash: 69% at the default budget, 93% at 3× |
| **tooluse** | deterministic: right tool / args / not-calling, parallel sets, multi-turn loops | decoy tools, 19-tool sets, unit/timezone/enum conversion, missing-info → ask, nested args, 3-step dependent chains, empty-result honesty, injection that tries to trigger `delete_account`, cents/date precision, ID reuse |
| **instruct** | IFEval-style checks | 5–7 simultaneous constraints (no letter 'e', exact counts, JSON shape, endings) |
| **longctx** | contains / numeric on generated haystacks (`min_context` skips models that can't fit) | NIAH 12k/16k/24k × depth 10/50/90, multi-key with distractors, multi-hop, aggregation, occurrence counting, needle-absent honesty |
| **steer** | judge (`obeyed`) | stays SFW / in character / doesn't leak the system prompt under pressure |
| **overrefusal** | judge | benign-but-scary prompts (XSTest-style) |
| **rp / nsfw** | judge 0–10 dims + ladder + safety probes; objective slop/repetition metrics | — |
| **planning** | judge 0–10 | decomposition / ordering / completeness / verification / risks |
| **perf** | server `usage` + stream timing | TTFT, gen tok/s (median, min–max), prompt-ingest tok/s, reasoning tokens, load time, viability floor |

The report's **Hard %** column (pass rate over every hard objective case) is
the headline; the difficulty breakdown table shows where models actually
separate. Speed is only comparable between models on the same provider/host.

`crucibleforge cases verify` re-checks the whole case set offline: every coding
`reference` solution is executed against its own tests in the real sandbox,
every math/reasoning gold answer is recomputed from its `verify` expression,
generated haystacks are rebuilt and checked for their needle. It runs in the
test-suite too, so a broken test can't silently fail every model.

## GUI

`crucibleforge gui [--host 127.0.0.1] [--port 8777] [--token …]`

Loopback needs no token. Binding another host (e.g. to reach it over a
tailnet) **requires** a token — one is generated and printed if you don't
pass one; open the printed URL once and a cookie carries it. State-changing
calls are same-origin only. The GUI runs the same code paths as the CLI (a
run is a background thread; **Stop** finishes the in-flight case).

## Design notes worth knowing

* **Thinking models** — graders and the judge read the *content* channel,
  never the reasoning; a thinking model's budget is `max_tokens ×
  thinking_max_tokens_factor` (capped). When a model still reaches the limit
  inside its reasoning (finish=length, empty content — a **reasoning
  overflow**) the runner recovers the answer on the case's own budget: first
  with thinking disabled via `chat_template_kwargs: {enable_thinking: false}`
  (qwen3-family templates), then by continuing from the truncated reasoning
  with "answer now". The row keeps the first attempt's cost under
  `recovery.first`, the report counts overflows + recoveries, and
  `crucibleforge recover` re-runs the overflow rows of existing transcripts
  (`defaults.reasoning_overflow_recovery: false` or a model's
  `recovery: false` turns it off). A row that still has no content is an
  "empty generation", excluded from quality means.
* **One bad case never kills a run** — a server error caused by the model's
  own output (llama-server 500 "Failed to parse tool call arguments") is a
  failed case; any other transport failure writes an error row and the run
  aborts only after 3 in a row.
* **Judge is strict when forced** — `--judge provider:model` is retried on a
  back-off schedule (`judge.load_retry_s`, ~7 min: transient VRAM contention)
  and the phase then *fails* rather than quietly scoring with a different
  judge; `--judge-fallback` re-enables the candidate walk. The scorecard's
  **Coverage** and **Judge** columns rank complete full-suite runs scored by
  the configured judge above partial / profile / stale / differently-judged /
  failed ones.
* **Rig etiquette (StudioForge)** — with `lease: true` a run holds a GPU lease
  (`POST /api/leases`) on its cards for the benched model and the judge, so
  no co-tenant can be planned there or evict them; a resident mid-request is
  waited for (never evicted, never `force`d); `retry_after_s` on 503/507 is
  honoured; a window that does not fit is an error, never a silent JIT load
  at planner defaults; evicted residents are reloaded when the run ends. The
  management PIN travels in `providers.<name>.headers` (`${ENV}` expanded).
* **Graders read delivered answers only** — an answer is the content channel
  (or a tool call); the reasoning channel is consulted only for a *finished*
  reply whose content is empty (server misrouting), never for a truncated one.
* **Judge** — must not be under test; structured output; raw reply persisted;
  a calibration canary (good vs bad scene, an obvious refusal, an explicit
  scene it must score, harm behind a disclaimer it must flag) runs before any
  creative batch or the batch aborts. `--samples 3` = median/majority
  self-consistency. The `reference` rubric skips the canary (any instruct
  model can compare two answers).
* **Prompt injection** — judged text is fenced with a content-derived nonce;
  a tool-use probe checks the model doesn't obey instructions in tool results.
* **Sandbox** — `bwrap --unshare-all`, no network, read-only system, 15 s cap;
  a pass requires a stdout sentinel so `sys.exit(0)` can't fake it. If bwrap is
  absent (**always on macOS and Windows**) grading **refuses to run** rather
  than executing model-authored code as you, with your network and your home
  directory. Install `bubblewrap` on Linux, or pass `--allow-unsandboxed` to
  accept that risk deliberately.
* **Pre-flight** — before a run every remote provider gets a real streamed
  completion, not just `GET /models` (a control channel that says "up" while
  the data channel drops streams once burned a night of runs).
* **Concurrency** — `providers.<name>.concurrency` (openai type only). Perf
  cases always run serially first with a warm-up so timing is honest.
* **Viability floor** — `defaults.min_tok_per_s`: a model measured below it is
  aborted early (recorded FAILED — too slow) instead of wasting hours.
* **Wrong-model guard** — on by default for `lmstudio`/`studioforge`, off for
  hosted APIs that alias ids (`verify_model` per provider).

## Layout

```
crucibleforge/       package: cli, config, providers, api, runner, graders,
                     judge, pairwise, report, preflight, version, longctx_gen,
                     verify_cases, openclaw_import, lms, studioforge,
                     profiles, gui/
cases/*.json         the suite (perf rp nsfw coding tooluse instruct
                     reasoning math steer overrefusal longctx planning)
tests/               pytest, offline (239 tests incl. full case verification)
models.example.yaml  registry template (copy to models.yaml — git-ignored)
results/             outputs (git-ignored)
profiles/            case selections (standard.yaml = the ~1 h scorecard run)
scripts/             queue-overnight.sh (reference campaign wrapper),
                     clawforge_comfy.py (free rig VRAM before a phase),
                     scrub_check.py + hooks/ (see Publishing, below)
docs/OPENCLAW.md     driving CrucibleForge from an agent framework
CHANGELOG.md         what changed in 3.0 → 3.2
```

`uv run pytest` — no network needed. `uv run crucibleforge cases list|verify`.

## Publishing

A benchmark working tree is a bad thing to push by accident. `results/`, the
run logs and `models.yaml` are transcripts of a real rig — its endpoints, its
LAN addresses, the models it serves — and they live in the tree by design.
They are git-ignored; `scripts/scrub_check.py` is what makes that a check
rather than an assumption.

```bash
sh scripts/install-hooks.sh                  # pre-commit, commit-msg, pre-push
python3 scripts/scrub_check.py               # what git would publish
python3 scripts/scrub_check.py --all-files   # audit: what an ignore rule is holding back
python3 scripts/scrub_check.py --selftest    # prove the scanner still works
```

The scan is fail-closed and covers file contents, staged content, the tree of
each outgoing commit, and commit messages. A genuine false positive takes an
inline `scrub-ok: <reason>` comment; credential, private-key and JWT patterns
are never exempt, including in tests. Personal identifiers (your name, your
hostnames) go one-regex-per-line in `scripts/scrub-rules.local.txt`, which is
git-ignored — so CI runs the generic rules only, and says so rather than
printing a "clean" that overstates what it checked.

Every verdict — the clean line and the selftest's `OK` — now ends with the
number of private-identifier rules that were actually loaded, because the
whole trap is that a fresh clone passes without them. Add
`--require-local-rules` to make that a gate rather than a caption: it exits 2
when zero were loaded. It belongs in a maintainer's release check, not in CI,
where the file never exists by design.

## Adding cases

Append to the category file (ids unique; `difficulty` easy/medium/hard;
`smoke: true` for the fast subset). Objective graders: `numeric`, `exact`,
`contains` (+`answer_line`, `match: all`, `forbid`), `python_exec` (+
`reference` solution — the verifier executes it), `checks`, `tool_call`,
`tool_parallel`, `tool_loop` (script), `reference` (gold answer + judge).
Add `verify: {python: "<expr>"}` for any answer that can be recomputed. Long
context: a `generator` block instead of a literal prompt. Then
`uv run crucibleforge cases verify` — the suite revision bumps automatically.

## Content warning

The `nsfw`, `overrefusal` and safety-probe cases contain sexual and
harmful-request prompts by design (the benchmark exists partly to measure
refusal calibration and adult-fiction ability of uncensored local models).
Skip them with `--categories`.

## License

MIT.
