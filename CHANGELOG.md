# Changelog

## 3.1.0 — 2026-08-22

Root-cause fixes from the first full-board campaign (seven 251-case runs).

- **Reasoning-overflow recovery**: a thinking model that spends its whole
  budget in the reasoning channel (finish=length, empty content — 27–45 rows
  per model, 10–18 % of a score) now gets its answer recovered on the case
  budget: `chat_template_kwargs: {enable_thinking: false}`, then a
  continuation of the truncated reasoning. Rows record `reasoning_overflow` +
  `recovery` (mode, attempts, first-attempt cost); the report counts them.
  New `gauntlet recover` re-runs only those rows of existing transcripts
  (same `bench_run_id`, so they supersede) ready for `gauntlet judge`.
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
- **Case verifier** (`gauntlet cases verify`, also in tests): reference
  solutions executed in the sandbox, gold answers re-derived from `verify`
  expressions, generated haystacks checked.
- **Web GUI** (`gauntlet gui`): dashboard, run control with live log and
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
