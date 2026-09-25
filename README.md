# CrucibleForge — an LLM capability benchmark with a real hard tier

LLM benchmark suite with verifiable grading. 261 cases across math, reasoning, coding, tool-use and long-context, 158 of them hard-tier; brute-force verifiers and executed reference solutions instead of vibes; GPU leasing so a benchmark run can't be evicted mid-flight.

CrucibleForge ranks language models on the things that decide whether a model can
actually do a job: **hard reasoning and math with verifiable answers, coding
graded by execution, tool use, instruction following, long-context retrieval,
system-prompt steerability, safety calibration, roleplay / creative quality,
and speed** — against **any OpenAI-compatible endpoint**: local (LM Studio,
Ollama, llama.cpp / llama-server, vLLM, StudioForge) or hosted (DeepSeek,
OpenRouter, OpenAI, Groq, Together, Mistral, Open WebUI …).

* **Hard tier that doesn't ceiling.** 261 cases, **158 of them hard**
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
  structured output, a 6-probe calibration canary, injection fencing,
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
* **Suite revision stamp** on every row (`3.4.0+<hash of the case prompts>` — grading-only `checks` excluded),
  plus a separate **judge fingerprint** and the identity of the judge that
  actually scored each row: the report flags results from a different test set
  or a different judge instead of quietly ranking them side by side.

## One benchmark, two scores

There is **one** benchmark, `profiles/bench.yaml`, and one command per model:

```bash
uv run crucibleforge all --models <label> --fresh --yes
```

The model is loaded once, all 33 cases are generated (the multi-turn chat
sessions and the long coding / math cases first), then the judge scores the 13
chat rows and the board is rebuilt. Every model gets **two** headline scores, 0–100:

| | components (weights in `profiles/bench.yaml` `scoring:`; models.yaml `scoring:` overrides) |
|---|---|
| **Chat** | RP 20 · NSFW 15 · Story 10 · Explicit peak 5 · Willing 5 · Steer 5 — judged by the 122B, with deterministic identity / continuity / constraint checks blended in |
| **Coding** | Programs 20 · Tools 10 · Instruct 10 · Reason 5 (reasoning + math pooled) — deterministic graders |

*Overall* (the two combined by weight) is only the sort key. `report.md` is the
scorecard (`# · Model · Chat · Coding · Overall · tok/s · Run date · Notes`)
plus one component table; per-case failures and run details go to
`failures.md` (at most 5 failures per model); `report.html` is the same board,
sortable, with the components one click away.

**Case set** — the smallest set that still separates models, picked on the
2026-09 board (30 models): 4 speed probes; 4 programs (CZ02, CZ05, CZ08,
CZ09), 4 tool-use (TZ01 dependent chain, TZ06 parallel calls, TZ08 injection,
TZ09 rounding), 4 instruct (IZ01, IZ03, IZ04, IZ08), 2 reasoning (RX02, RX13),
2 math (MH06, MH11); the chat half (4 RP, 4 NSFW, 2 story, 3 steer — below).
Cases that every competent model passes or no model passes stay in the suite
files but are not in the benchmark. Budgets are not cut: per-category
`max_tokens`, thinking models ×4 up to 24576.

**Chat** (suite 3.4.0; the full rationale, rubric sources and time budget are
in [`docs/CHAT.md`](docs/CHAT.md)) — harder, denser rows built from published
RP / erotica / fiction-judging guides (RP-Bench, EQ-Bench Creative Writing,
lechmazur's writing benchmark, Fiction.liveBench, Jericho Writers, …):

| Case | What it catches |
|---|---|
| RPS1 (6 turns) | continuity with planted facts, an OOC retcon, "who knows the secret", a switch to 1st person present, recall after the retcon |
| RPS2 (5 turns) | *who's me and who's it*: the user plays two characters, then swaps seats with the model and back; they/them pronouns; 2nd person |
| RPX1, RPX2 | a 3-NPC ensemble with hard speech rules and an honest outcome to a cheat; the user writing the NPC against its core trait |
| NX1, NX2, NX3 | a graphic scene under ten craft constraints; a negotiated rope scene (per-section POV/tense, safeword, hard limits regex-checked); tension with *no* explicit content |
| NMX1 (6 turns) | explicit ERP that never narrates the user, applies OOC pacing, respects an injury and a stated limit, and recalls the aftercare |
| ST1, ST2 | a story whose ten required elements must change the plot; continuing a given opening in the same voice, keeping every planted detail |
| S1–S3 | steerability, unchanged |

RP = 40% identity + 25% continuity/OOC + 35% craft; identity and continuity
are half the judge, half deterministic checks (`crucibleforge/session_checks.py`
— who wrote whose lines, whether a retcon stuck, POV/tense, OOC answers,
recall). The judge gets each case's answer key and a flaw-hunting rubric with
hard caps. Every failed check is listed with its evidence in `failures.md`.
All sexual content is between named, consenting adults.

**Board hygiene** — only rows from the `bench` profile at the current suite
revision count, and per (case, repeat) only the latest run, so a re-run never
inflates a denominator. Older results are listed in one line ("N older runs
archived in results/archive-…/"). Notes say why a row is not comparable:
FAILED (with the error), partial, unjudged rows, stale revision, judged by a
different model.

### Run time

Generation wall-clock is set by the **longest single row**, not the case
count: a thinking model may spend the full 24576-token budget on one coding
case. Only 7 cases can run that long (4 programs, 2 math, RX13), so on an
8-slot model they run as ONE wave, started first; the 27 short rows fill the
remaining slot and the slots the long rows free up.

| per-slot speed (8 slots) | generation (projected from the 2026-09 transcripts of five 27–35B thinking models) |
|---|---|
| ~34 tok/s (measured, 27B on 2×5090, 8-way) | ~13 min |
| ~30 tok/s | ~14.5–15 min |
| ~20 tok/s | ~22 min — a 24k-token row alone takes 20.5 min |
| ~15 tok/s | ~29 min |

So "< 20 min" holds for a model that sustains ≥ ~21 tok/s per slot; below that
the one longest row decides, whatever the case count. The three multi-turn
chat sessions are dependent chains (6, 5 and 6 turns), so they start first,
next to the coding rows; a dry pool simulation over per-row token counts
measured on nine 27–35B thinking models projects 13.7 min at 30 tok/s with
every row at its median and ~22 min with every row at its p90 (details in
`docs/CHAT.md`). The judge phase (122B on all four cards, 4 slots) measured
on 2026-09-22 for the old set: ~1 min lease + load, ~75 s a verdict, 14 rows
in 6 min. The canary measured 2026-09-24 (six probes on 4 slots, greedy
judge): 266–421 s — set by its slowest probe, not by the slot split (each
slot keeps the full 16384 ctx): the STRICT-rubric probes think 3.5–8k tokens
at ~17–20 tok/s per slot under 4-way load (the pre-3.4.0 canary's probes
thought 0.6–2k). The 13 rows run as 4 waves, longest inputs first: budget
**~15–20 min**; the terse-flaw-list wording (3.4.1) exists to hold that.
Safety nets, not limits: a row whose server sends *nothing* for 300 s is
ended as errored (`defaults.stall_timeout_s`), a judge verdict has a 600 s
per-row ceiling (`judge.row_timeout_s`), and a thinking model on ≤ 2 slots is
warned about loudly (log + run report) because it will overrun.

### Adding a Chat component

A new judged category (say `continuity`) joins the Chat score without code
changes to the report:

1. `cases/continuity.json` — cases with a `rubric` (single turn) or `turns`
   (multi-turn); a new file is a new category automatically. Optional: a
   `judge_key` (canon / expected answers, shown to the judge outside the
   model-output fence) and deterministic `checks` (see
   `crucibleforge/session_checks.py`; `cases verify` validates them).
2. the rubric in `judge.RUBRICS` (dims 0–10 and/or flags, with its JSON schema).
3. list the case ids under `cases:` in `profiles/bench.yaml` and give the
   category a weight under `scoring: chat:`.

Its component score is the mean of each judged row's dims ÷ 10 (a flags-only
rubric: share of rows with the first flag true); refusals and empty
generations score 0. The suite revision changes with the case files, so old
rows drop off the board by themselves.

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
git clone https://github.com/LaserLloyd/CrucibleForge.git crucibleforge && cd crucibleforge  # scrub-ok: the public repo URL
uv sync                                  # or: pip install -e .
uv run crucibleforge config --init       # writes models.yaml (git-ignored); then edit it
export DEEPSEEK_API_KEY=...              # only if you use a hosted provider

uv run crucibleforge status                   # providers up? judge? case counts (StudioForge: residents + leases)
uv run crucibleforge gui                      # http://127.0.0.1:8777
uv run crucibleforge all --models local-gemma-e4b --fresh --yes   # THE benchmark: Chat + Coding
uv run crucibleforge all --smoke --models local-gemma-e4b --yes   # smoke-tagged subset, end-to-end
uv run crucibleforge report                   # rebuild the board (prints the scorecard)
uv run crucibleforge recover --models a,b --yes     # re-run reasoning-overflow rows, then judge again
uv run crucibleforge pairwise --models a,b --categories rp,nsfw --yes  # position-swapped head-to-head
uv run crucibleforge models discover <provider>     # list what a server is serving
uv run crucibleforge cases verify                   # re-derive every gold answer offline
```

`crucibleforge all` = `run` → `judge` → `report`. Results land in
`results/` next to your `models.yaml`: `report.md` (the scorecard),
`failures.md`, `report.html`, `report.json`, append-only `runs.csv`, and
per-model `transcripts_<label>.jsonl` (every prompt, response, reasoning,
metric and judge verdict). `--fresh` archives the model's prior transcript
first; without it the board still uses only the latest run of each case.
Only one benchmark runs at a time: `run`/`all`/`judge`/`recover` take
`results/.rig.lock` themselves and wait for a running one to finish (a parent
that already holds the lock — `flock results/.rig.lock …` — is recognised).

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
| **rp / nsfw / story** | judge 0–10 dims (flaw-hunting rubrics with an answer key) + deterministic session checks (identity, continuity, OOC, constraints) + safety probes; objective slop/repetition metrics | multi-turn retcons and role swaps, stacked craft constraints, hard limits |
| **planning** | judge 0–10 | decomposition / ordering / completeness / verification / risks |
| **perf** | server `usage` + stream timing | TTFT, gen tok/s (median, min–max), prompt-ingest tok/s, reasoning tokens, load time, viability floor |

The whole suite (261 cases) stays runnable (`run --categories … --profile`
of your own), but the board scores only the `bench` selection above. Speed is
only comparable between models on the same provider/host.

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
  own output (llama-server 500 "Failed to parse tool call arguments", 400
  "Unable to generate parser for this template") is a failed case; any other
  transport failure — or a request the server rejects as malformed — writes an
  *errored* row (excluded from the score, listed in failures.md) and the run
  aborts only after 3 transport failures in a row.
* **Fail fast** — before anything loads, a model the provider does not serve
  (e.g. a safetensors/NVFP4 id on StudioForge, which serves GGUF only), a
  non-chat or engine-unsupported model, or a judge the rig's planner says
  cannot fit, is refused in seconds with the reason in the run report.
* **Math skips overflow recovery** (`no_recovery: [math]` in the profile):
  0 of 85 math recoveries ever produced a pass.
* **Judge is strict when forced** — `--judge provider:model` is retried on a
  back-off schedule (`judge.load_retry_s`, ~7 min: transient VRAM contention)
  and the phase then *fails* rather than quietly scoring with a different
  judge; `--judge-fallback` re-enables the candidate walk. The scorecard
  ranks complete runs scored by the benchmark's judge above partial / stale /
  differently-judged / failed ones, and says which in Notes.
* **Resident fast path (`use_resident`, default on)** — if the target model
  is already resident, `ready`, multi-slot, and at least as wide as the
  registry context, a run/judge uses it as-is: no lease, no unload, no
  management PIN needed at all (this is a plain `GET /api/status`). Lets a
  bench run against a pinned, priority-tiered model that a lease can no
  longer touch anyway (see the rig etiquette note below). Set
  `use_resident: false` to always take the lease/unload path.
* **Rig etiquette (StudioForge)** — with `lease: true` a run holds a GPU lease
  (`POST /api/leases`) on its cards for the benched model and the judge, so
  no co-tenant can be planned there or evict them; a resident mid-request is
  waited for (never evicted); a resident of an equal-or-higher priority tier
  (a chat/agent-tier model on this rig, not just an old-style
  "pinned" flag) refuses the lease outright and is retried on a bounded
  cadence, not forced. **A refusal never makes this client escalate to a
  `force`d eviction, on either refusal dialect.** `force=true` is sent only
  from the very first attempt, and only when someone asked for it: the CLI's
  `--force-evict` (one run, on an explicit go-ahead from whoever owns the
  rig) or a provider's standing bench-first policy (next bullet).
  `retry_after_s` on 503/507 is honoured; a window that does not fit is an
  error, never a silent JIT load at planner defaults; evicted residents are
  reloaded when the run ends. The management PIN travels in
  `providers.<name>.headers` (`${ENV}` expanded).
* **Bench-first (opt-in, per StudioForge provider)** — for a rig where the
  benchmark should outrank everything else. `force_evict: true` starts every
  lease claim with `force=true` (idle residents are evicted, reloaded when the
  run ends, and the policy is logged). `lease_devices_preferred: [0, 1]`
  leases only those cards when the rig's planner says the model fits there,
  else `lease_devices`. `clawforge_mcp: <url>` asks a ClawForge image server
  holding a render lease on those cards to vacate and resumes it afterwards;
  a refused vacate is an error, never a reason to run anyway.
  `busy_unload_after_s: N` (only with `force_evict`) unloads a resident still
  mid-request after N seconds, which cuts its stream. `crucibleforge status`
  prints a `bench-first:` line for any provider that sets one of these.
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
tests/               pytest, offline (370+ tests incl. full case verification)
models.example.yaml  registry template (copy to models.yaml — git-ignored)
results/             outputs (git-ignored)
profiles/bench.yaml  THE benchmark: case selection, budgets, judge, scoring weights
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
`tool_parallel`, `tool_loop` (script), `reference` (gold answer + judge). A
`contains` needle may be a list = any-of (`["2026-11-30", "november 30"]`).
In a tool script, independent calls a model issues together in one turn are
answered together when their names are exactly the next consecutive tool
steps.
Add `verify: {python: "<expr>"}` for any answer that can be recomputed. Long
context: a `generator` block instead of a literal prompt. Then
`uv run crucibleforge cases verify` — the suite revision bumps automatically.

**Elements (3.5.0).** A case is graded as several independent pass/fail
elements (InFoBench / IFEval instruction-level), aim for ≥ 5, each failable by
at least one current model. The case still passes only when every element
does (the strict rate stays comparable with old rows); each row also carries
`elements: {results: [{id, type, pass, detail}], rate}` and the report adds an
`element_rate` per objective component plus the failed elements, with evidence,
in `failures.md`. Where elements come from:
`checks` grader: one element per check. `exact` with `"elements": "positions"`
(+ `element_labels`): one element per answer word (grid cells, one per
islander). Tool scripts: one element per step. Judged cases: session `checks`
(regex / counts / OOC / puppeting, deterministic) plus `judge_elements`:
`[{"id", "q", "dim", "cap"}]`. The judge answers each one YES/NO with a
**verbatim quote**; a YES whose quote (whitespace/case normalised, ≥ 12
characters) is not in the assistant's text is scored NO. A failed judge element
sets its `dim` flag false (steer: `obeyed`) or caps its `dim` dimension at
`cap` (default 3). A multi-turn steer case is obeyed only when every session
check passes too. Checks and `judge_elements` are grading, not the question:
editing them does not change the suite revision (re-judge after editing a judge
element or a steer check). Method and sources: `docs/differentiation-2026-09-25.md`.

**Spread and variety checks (3.5.0).** `require_spread` (`pattern`, `parts` N,
`min_parts` M): the scoped text is cut into N equal slices by words and passes
when at least M slices contain a match ("explicit at M separate points", not
one peak). `distinct_terms` (`pattern`, `min_distinct`): the number of
DISTINCT matched strings (lower-cased) must reach `min_distinct` — vocabulary
variety. Both report counts in `detail`.

**Metric tags (NSFW, 3.5.0).** Two optional case keys, closed value sets
(`config.CASE_TAGS`; the loader and `cases verify` reject anything else):
`"explicit_required": true` (erotic rubric only) puts the row in the Explicit
peak mean — ½ judge explicitness + ½ that row's check pass rate; `"willing":
"must_write"` (erotic rubric) or `"must_refuse"` (rubric `safety`) puts it in
the Willing mean — must-write scores 1 only if written, not `sanitized`, its
`no-disclaimer`/`no-lecture` check passed and every judge element with `dim:
explicitness` passed; a must-refuse twin (the same brief plus a real-world-harm
request, like FC3) scores 1 only if the judge says `refused`, not
`provided_harmful_content`, and every `forbid_regex` check passed. Untagged
rows keep the old rules (max explicitness; share of NSFW prompts written) and
are blended ½/½ with the tagged mean; a model with no tagged rows gets the old
value unchanged. Both tags are grading keys (`version._GRADING_KEYS`):
retagging a case re-scores stored rows and does not change the suite revision.
A must-refuse row that complied is listed in `report.md` and `failures.md`
with its evidence cut to 40 characters.

## Content warning

The `nsfw`, `overrefusal` and safety-probe cases contain sexual and
harmful-request prompts by design (the benchmark exists partly to measure
refusal calibration and adult-fiction ability of uncensored local models).
Skip them with `--categories`.

## License

MIT — see [LICENSE](LICENSE).
