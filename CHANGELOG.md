# Changelog

## Unreleased — renamed to CrucibleForge

The project is now **CrucibleForge** (was Gauntlet), matching the StudioForge /
ClawForge naming on this fleet. Package `crucibleforge`, CLI verb
`crucibleforge`, env vars `CRUCIBLEFORGE_CONFIG` / `CRUCIBLEFORGE_RESULTS` /
`CRUCIBLEFORGE_ENV_FILE`, GUI token header `X-CrucibleForge-Token`, and the
StudioForge GPU **lease holder string is now `crucibleforge`**. Out-of-repo
co-tenants that yield to the lease (`an hourly image job`) recognise both the new and the
old holder so an in-flight lease is never left unrecognised. Historical run
logs and `results/` rows keep their original wording.

## Unreleased — lease hardening from the 2026-08-23/24 campaigns

Field fixes on top of 3.2.0, all found by running real campaigns against the
rig while other tenants were on it. Version string stays 3.2.0 (no release).

- **Lease takes the placement, not just the cards** (`studioforge.py`): the
  lease itself sizes a model at `parallel=1`, so after acquiring it the run
  re-plans through `POST /api/models/<id>/load-recommended` — otherwise every
  leased run benchmarked a 1-slot serial placement and the concurrency the
  provider advertises never happened. Lease loads are silent (no duplicate
  progress lines).
- **`load-recommended` is satisfied by a bad resident.** It treats "already
  loaded at that context" as done and returns the *existing* plan, so a
  leftover JIT load (serial, on the slow cards) survived into a full
  benchmark run on 2026-08-24. A degenerate resident (1 slot / short context)
  is now unloaded first so the server re-plans it.
- **A pinned idle resident blocks the lease**: retried with `force: true`
  only in that case — the rig's pin reconciler restores the pinned model
  after the lease is released. Busy residents are still never forced.
- **Restore** unloads what the run loaded before reloading the prior
  residents, instead of asking the rig to hold both at once.
- **SIGTERM releases the lease.** A killed queue used to leave the cards held
  until the TTL expired.
- **`answered_in_reasoning`**: a tool call with empty `content` is an answer,
  not a misrouted reply — this was stamping false "read from reasoning" notes
  on perfectly good tool-use rows.
- `scripts/clawforge_comfy.py`: free rig VRAM (ComfyUI) before a phase.
- `scripts/queue-overnight.sh`: the reference campaign wrapper (flock on
  `results/.rig.lock`, env sourced in-shell for the lease PIN, run→judge per
  model, one report, rc checked per phase).
- Docs: README case counts corrected to the real 251 / 158 hard and the
  56-case `standard` profile.

## 3.2.0 — 2026-08-22 (evening)

Second revision from the campaign postmortem: a swarm review (5 lenses, every
finding adversarially verified — 39 confirmed) plus the rig's own
`BENCHMARKING.md` playbook, re-checked against the live StudioForge 0.2.0 API.

- **Rig etiquette / GPU leases** (`studioforge.py`, `providers.py`): with
  `lease: true` a run takes `POST /api/leases` on the configured cards (holder
  `crucibleforge`) for the benched model and again for the judge, keeps it alive,
  releases it on exit; busy residents are *waited for* (`wait_busy_s`), never
  evicted, never `force`d (the old client unloaded everything and sent
  `force: true` — the one client on the box that could rip a family bot's
  model out mid-reply); evicted residents are reloaded at the end
  (`restore_residents`); the `X-MCP-Pin` header reaches every management call
  (`${ENV}` in `headers`). `507`/`503` bodies are read: `retry_after_s` is
  honoured (`api.VramContention`), per-mode `suggestions` are surfaced, and a
  window that does not fit is an error — never a silent JIT load at planner
  defaults. Placement profiles use the 0.2.0 nested `optimal` shape and keep
  `devices`. Eviction detection: `is_loaded()` compares the live plan (slots,
  ctx, devices) with the one the run loaded. `wait_ready()` tolerates
  transient status errors and gives up after 60 s when the model never
  appears. `crucibleforge status` prints residents + leases.
- **Runner**: a recovered TOOL CALL counts as recovered; a failed recovery
  request keeps the honest first result and does not count toward the
  transport-abort; every attempt is billed on the row (cost/tokens);
  `chat_template_kwargs` merge one level deep; a negative thinking probe no
  longer disarms auto-detect (gemma-e4b ran every case on the raw budget);
  `reasoning_format` in `extra_body` means thinking. A model-local abort no
  longer sets the global STOP (the rest of the batch used to be skipped
  silently). **Graders never harvest an answer from truncated reasoning**
  (finish=length): 9 of dark-scarlett's 51 coding "passes" were code dug out of
  100k chars of cut-off chain-of-thought that no user ever received. The
  registry/live context mismatch is detected and long-context cases skipped
  honestly. `crucibleforge recover` re-runs each (run, case, repeat) triple, logs
  unselectable jobs, never rewrites the run's meta (appends `recovered`).
- **Judge**: per-row isolation (`RequestRejected`/`GenerationRejected` → a
  failed verdict without a reload; any other error → counted `errored`, row
  left for a re-run; reload guarded by a lock and skipped when another worker
  already restored the judge); a thinking judge that reasons its budget away
  is re-asked with thinking off; failed verdicts keep `judge_finish_reason`/
  tokens/reasoning tail; a judge failure on a reference row leaves the row
  **pending** instead of failing the model.
- **Revision/judge stamp**: `bench_revision` now hashes the test set only;
  the primary judge's measurement settings are a separate
  `judge_fingerprint`; rows stamped with the pre-3.2 combined hash stay
  current while nothing changed (`legacy_revision`).
- **Report**: Coverage ranks full-suite runs scored by the configured judge
  first; partial / profile / stale / differently-judged / failed rows are
  labelled (with `attempted`/`n/a` counts) and ranked below; new **Judge**
  column (`self` for self-graded reference rows); Summary table carries
  Coverage and the scorecard order; a one-half Total says "(Code only)";
  Willing counts empty replies as unwritten and every rate cell shows
  (scored/total); family-overlap note compares each model with *its* judge.
- CLI exit codes: `run`/`recover` are non-zero when any requested model
  failed or never ran; `judge` when rows errored. Queue scripts stamp DONE
  only on clean exits and serialise on a lock.

## 3.1.0 — 2026-08-22

Root-cause fixes from the first full-board campaign (seven 251-case runs).

- **Reasoning-overflow recovery**: a thinking model that spends its whole
  budget in the reasoning channel (finish=length, empty content — 27–45 rows
  per model, 10–18 % of a score) now gets its answer recovered on the case
  budget: `chat_template_kwargs: {enable_thinking: false}`, then a
  continuation of the truncated reasoning. Rows record `reasoning_overflow` +
  `recovery` (mode, attempts, first-attempt cost); the report counts them.
  New `crucibleforge recover` re-runs only those rows of existing transcripts
  (same `bench_run_id`, so they supersede) ready for `crucibleforge judge`.
- **Per-case failure isolation**: llama-server 500 "Failed to parse tool call
  arguments" (the model's own malformed output) is `GenerationRejected` — not
  retried, scored as that case failing; other transport errors write an error
  row and abort the model only after 3 in a row. Previously one such 500
  aborted the entire model run (joyfox-35b-rp, twice).
- **Strict forced judge**: `--judge` is retried on `judge.load_retry_s`
  (VRAM contention from a co-tenant on the rig is transient) and then fails
  loudly; it no longer silently scores a model with the next candidate
  (which broke the single-judge rule and mixed judge families on the board).
  `--judge-fallback` opts back in.
- **Judge counts**: "parse-failures" no longer includes empty generations;
  the two are reported separately (`failed` vs `empty`).
- **Coverage on the scorecard**: complete runs rank first; partial (smoke /
  category-filtered / hard-only), FAILED and stale-revision rows are
  labelled and ranked below, whatever their Total.
- **StudioForge readiness**: after an explicit load the client waits for the
  engine to report `ready` before the warm-up completion (a warm-up sent
  during `loading` made the server plan a second load that then 507'd).

## 3.0.0 — 2026-08-18 (Gauntlet)

Rebuilt from the v2.2 `bench` tool as a publishable, provider-agnostic module.

- **Providers** (`openai` / `lmstudio` / `studioforge`) replace the hard-wired
  LM Studio + StudioForge backends: any OpenAI-compatible endpoint (DeepSeek,
  OpenRouter, OpenAI, Ollama, vLLM, llama-server, Open WebUI …), API keys via
  `api_key_env`, per-provider concurrency, `extra_body`, relaxed wrong-model
  guard for hosted APIs, per-row **cost** for priced models.
- **Judge local or remote**: judge candidates reference a provider;
  `--judge provider:model_id` override; concurrent judging on remote APIs;
  new `reference` rubric (gold answer → correct/incorrect) that any small
  instruct model can score.
- **Hard tier de-ceilinged**: new `math` category (22 competition-style
  problems, each brute-force verified), 18 hard reasoning cases (logic grids,
  knights & knaves, state tracking, ciphers, counting, BFS, bug-finding /
  gold-answer prose questions), 16 hard coding cases with complexity-enforcing
  tests and executed reference solutions, 13 hard tool-use cases (decoys,
  large tool sets, unit/timezone/enum conversion, missing-info → ask, nested
  args, dependent chains, empty-result honesty, delete_account injection,
  cents/date precision, id reuse), 5 multi-constraint instruct cases, and a
  generated 12k–24k-token long-context tier (NIAH grid, multi-key, multi-hop,
  aggregation, counting, needle-absent) with `min_context` skipping.
- **Case verifier** (`crucibleforge cases verify`, also in tests): reference
  solutions executed in the sandbox, gold answers re-derived from `verify`
  expressions, generated haystacks checked.
- **Web GUI** (`crucibleforge gui`): dashboard, run control with live log and
  stop, report view, per-row drill-down, provider/model/judge management,
  model discovery, connection tests. Stdlib server, vanilla JS, no CDN;
  token auth required off-loopback, same-origin POSTs.
- **CLI**: global `--config`/`--results`, `config --init/--upgrade`,
  `import-openclaw`, `models discover|add`, `cases list|verify`.
- Report: **Hard %** headline, Math and Long-context columns, cost column,
  "n/a" for context-skipped cases, provider instead of device.
- Config search path + results next to the config; v2 registries upgraded
  in memory; `models.yaml` git-ignored, `models.example.yaml` shipped.
- Fixed: stale LM Link preflight (now a per-provider data-channel probe).
