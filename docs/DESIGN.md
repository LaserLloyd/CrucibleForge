# Design notes worth knowing

[README](../README.md) · [Install](INSTALL.md) · [Running](RUNNING.md) · [Config](CONFIG.md) · [Scoring](SCORING.md) · [CLI](CLI.md) · [Design notes](DESIGN.md) · [Cases](CASES.md) · [Developing](DEVELOPING.md)

How the harness behaves at the edges: thinking models, failing servers, judge strictness, the sandbox and
GPU etiquette on a shared server.

* **Thinking models, as deployed** — graders and the judge read the
  *content* channel, never the reasoning. Each model runs with its maker's
  recommended sampler and default thinking; every model gets the same output
  budget per row (case `max_tokens` × 4, floor 16,384, cap 24,576; the function cases and the code-bank
  rows carry their own 65,536 budget). There is no
  thinking-off retry: an answer that never arrives scores as missing, and a
  row that ran out of budget counts as failed (the `--escalate` retry only
  feeds Room).
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
  a calibration canary (seven known-answer probes: good, bad, refusal, explicit scene, partial, godmod and a
  long input) runs before any creative batch or the batch aborts. `--samples N` = median/majority
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
* **Credit preflight (4.1)** — before a run, each hosted provider gets one cheap request (DeepSeek: its balance
  endpoint; others: a one-token completion where only a billing answer counts). An empty account stops that
  model at once, by provider name.
* **Pre-flight** — before a run every remote provider gets a real streamed
  completion, not just `GET /models` (a control channel that says "up" while
  the data channel drops streams once burned a night of runs).
* **Concurrency** — `providers.<name>.concurrency` (openai type only). Perf
  cases always run serially first with a warm-up so timing is honest.
* **Viability floor** — `defaults.min_tok_per_s`: a model measured below it is
  aborted early (recorded FAILED — too slow) instead of wasting hours.
* **Wrong-model guard** — on by default for `lmstudio`/`studioforge`, off for
  hosted APIs that alias ids (`verify_model` per provider).
