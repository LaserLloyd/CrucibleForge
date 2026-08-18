# Changelog

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
