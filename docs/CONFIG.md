# Configuration and backends

[README](../README.md) · [Install](INSTALL.md) · [Running](RUNNING.md) · [Config](CONFIG.md) · [Scoring](SCORING.md) · [CLI](CLI.md) · [Design notes](DESIGN.md) · [Cases](CASES.md) · [Developing](DEVELOPING.md)

Everything CrucibleForge needs to know lives in one file, `models.yaml`: **providers** (where a server is),
**models** (which model on it) and a **judge**. Keys never go in it; a provider names an environment variable
with `api_key_env`. Create the file with `uv run crucibleforge config --init`.

## Backends and APIs

CrucibleForge is a client. Everything it needs is in `models.yaml`: a **provider** (where a server is), a
**model** (which model on it) and a **judge**. Run `uv run crucibleforge config --init` to get a file with all
of the examples below already in it.

### Local server

Any server that speaks `/chat/completions` works with `type: openai`:

```yaml
providers:
  llamacpp:                     # llama-server, LM Studio, Ollama, vLLM, StudioForge gateway, ...
    type: openai
    base_url: http://127.0.0.1:8080/v1

models:
  - name: my-model
    provider: llamacpp
    model_id: the-id-your-server-reports
    context_length: 32768
```

Common addresses: llama-server `http://127.0.0.1:8080/v1`, LM Studio `http://localhost:1234/v1`, Ollama
`http://localhost:11434/v1`, vLLM `http://localhost:8000/v1`. A StudioForge server's OpenAI API is its gateway
(port 1234 by default), never the control panel port. If the server requires a key, add `api_key_env: <ENV VAR NAME>`.

Two richer provider types exist for local rigs: `type: lmstudio` (managed through the `lms` CLI, one model at a time,
with a wrong-model guard) and `type: studioforge` (loads models through the server's planner, runs as many
requests at once as the server has slots, and can take a GPU lease for the run).

### Hosted API

```yaml
providers:
  deepseek:
    type: openai
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY      # the NAME of an environment variable, never the key
    concurrency: 8
  openrouter:
    type: openai
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    concurrency: 8

models:
  - name: deepseek-flash
    provider: deepseek
    model_id: deepseek-flash
    context_length: 131072
    price: {input: 0.14, output: 0.28}   # USD per 1M tokens; feeds the report's Run cost column
```

**Run cost.** The report (`report.md`, `report.html`, `report.json`) shows what ONE run of the standard Code and
Chat tests cost at the provider's price, per hosted model: the recorded per-request cost, or, for a row with none,
its token counts times `price`. It leaves out the NSFW tests (kept in separate `cost_separate_usd` fields), bonus
sets, retries and re-runs, the judge (it runs locally), and cache or off-peak discounts. A provider billed by a flat
plan instead of per token adds `basis: list` to the price block (`price: {input: 0.15, output: 0.60, basis: list}`);
its figure shows as `~$0.42`, an estimate at list price. `$0.66*` means some requests returned no usage data, so the
figure is partial. Local models show `—`. A hosted model without a `price` is refused at run start unless you pass
`--allow-unpriced`; a free endpoint says `price: {input: 0, output: 0}`.

**Blocked replies.** When a provider's content filter blocks a reply (`finish_reason: content_filter`), the model's
Notes say how many (and how many were in the NSFW tests, where they count as refusals). A blocked reply scores zero.
This is computed from the transcripts on every report, so future runs are marked with no hand work. A label's first
generation whose first 3 replies were all blocked stops with `first 3 replies all blocked by the provider's content
filter` (a provider refusing everything, such as a wrong key tier or region), not the empty-reply "misconfigured
model" error; blocked replies are otherwise ignored by that empty-reply guard.

`models.example.yaml` also has entries for OpenAI, xAI, MiniMax and Open WebUI. Claude works directly: Anthropic's
OpenAI-compatible endpoint is `type: openai` with `base_url: https://api.anthropic.com/v1`. A vendor with no
OpenAI-style endpoint of its own can usually be reached through OpenRouter. Hosted providers should carry `docs:` and
`docs_verified:` so the setup check can tell the request format was read from the vendor's documentation.
Hosted-API models are only completed by `bench-all` with `--include-api`, because they cost money.

### Already use OpenClaw?

`uv run crucibleforge import-openclaw --write` copies providers, models, prices and `${ENV}` key references from an
OpenClaw config. Literal keys are never copied. See [`docs/OPENCLAW.md`](OPENCLAW.md).

## Registry (`models.yaml`)

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
* `reasoning_replay: true|false` (per provider) passes the model's **reasoning
  chain back to it**: the assistant turn goes back with its `reasoning_content`,
  and every request carries `reasoning_split: true` so the thinking arrives in
  that channel for every model on the provider. MiniMax documents that the
  complete assistant message — reasoning included — must be appended to the
  history or the chain is lost (measured: 1/10 recall with it dropped vs 7/10
  replayed). **Default: on for a provider pointed at MiniMax's API, off for
  every other provider** — off means every request is byte-identical to a run
  without the switch. MiniMax facts: the API **ignores `temperature` and
  `top_p`** (it always uses 1 / 0.95); **M2.5 / M2.7 retire on 2026-11-18**,
  and M Plan / Token Plan traffic to M2.5 / M2.7 is **auto-routed to M3.1
  Flash** from 2026-10-19 (CF cannot tell a route from the real model — `/v1`
  returns the *requested* id).
* `docs: <url>` + `docs_verified: YYYY-MM-DD` (per provider) — for a
  **hosted** provider: where its API documentation was read, and when it was
  last reviewed. `conform` WARNs (c7) when a hosted provider carries no docs,
  or when the review is older than 120 days — the "review the vendor docs"
  step becomes visible instead of being silently skipped.
* Config search order: `--config`, `$CRUCIBLEFORGE_CONFIG`, `./models.yaml`,
  `<repo>/models.yaml`, `~/.config/crucibleforge/models.yaml`. A v2 registry
  (`endpoint:`/`rig:`) is upgraded in memory; `crucibleforge config --upgrade`
  rewrites it.
* **OpenClaw users:** `crucibleforge import-openclaw --write` pulls providers,
  models, prices and `${ENV}` key references straight from
  an OpenClaw `openclaw.json` (remote providers by default,
  `--include-local` for loopback ones). Literal keys are never copied.

## What it never benches

The suite scores text answers to chat-formatted prompts, so a model is a
contestant only when its input and output are text and it chats. `bench-all`
never registers, and never benches, a rig model whose id names an
out-of-scope kind: translation (MT), captioners/OCR, 3D/GLB/CAD generators,
TTS/ASR/speech/audio/music, image/video generation, embeddings, rerankers,
classifiers, guard/safety and reward models (`bench_all.out_of_scope`). A
registry row with `bench: false` is never benched either (its rows are pruned
from the board unless `keep_on_board`). A vision-language chat model may be
registered on purpose (`--only`); mark it `modalities: text+image->text` and
its rows are published with `text_suite_only: true` (only the text suite was
measured). A model whose conformance record FAILs keeps its numbers on the
board but is **provisional**: unranked, with `provisional_reason` in the
public `report.json`, until a re-run under the right protocol passes (a
`models.yaml` row may set its own `reasoning_replay` when c3 proves the
provider default wrong for it; rows measured under the other setting are then
regenerated).

`crucibleforge cases verify` re-checks the whole case set offline: every coding
reference solution is executed against its own tests in the real sandbox (and
every mutant must fail them), every math/reasoning gold answer is recomputed,
generated haystacks are rebuilt. It runs in the test suite too.
