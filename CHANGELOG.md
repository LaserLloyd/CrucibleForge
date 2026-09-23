# Changelog

Versions 3.0.0–3.2.0 were developed privately under the name **Gauntlet**; the
project was renamed to CrucibleForge before its first public release. Nothing
was ever published as Gauntlet, so there is no migration to do — the entries
below are kept because the engineering they record is real, not to narrate a
rename.

## 3.3.0 — one benchmark, two scores (2026-09-23)

Suite revision **3.3.0** (cases + graders changed; every row at an older
revision is off the board — the 2026-09 results were moved to
`results/archive-2026-09-23/`, nothing deleted).

**One benchmark.** `profiles/bench.yaml` replaces `standard` / `coding` /
`chat` and is the default for `run` / `all` / `judge` / `recover` (and the
GUI). One command per model: `crucibleforge all --models <label> --fresh --yes`.
34 cases, chosen on the 2026-09 board (30 models) as the smallest set that
still separates models: programs CZ02 CZ05 CZ08 CZ09; tools TZ01 TZ06 TZ08
TZ09; instruct IZ01 IZ03 IZ04 IZ08; reasoning RX02 RX13; math MH06 MH11; the
chat half (6 RP, 5 NSFW, 3 steer) and the 4 speed probes (now 1 repeat).
Dropped from the benchmark (still in the suite): cases no model passes (CZ01,
CZ03, MH16), MH22 (1/30), MH01/MH20/CZ04/CZ10 (few passes, long), and cases
(nearly) every competent model passes (TZ02/03/04/05/07/10, IZ02/05/06/07,
RX08, RH5, CZ07, CY01).
Budgets unchanged (thinking ×4 up to 24576) — the time box comes from fewer
cases, not from limiting models.

**Two scores.** The board is **Chat** (RP, NSFW, explicit peak, willing,
steer — judged) and **Coding** (Programs, Tools, Instruct, Reason =
reasoning + math — deterministic); Overall is only the sort key. The coding
*category* is shown as "Programs". The capped T/S score is gone (tok/s stays).
Weights live in the profile's `scoring:` block (models.yaml `scoring:` still
overrides; the old `code:` key is accepted). A new judged category joins Chat
by getting a case file, a rubric and a weight — the report scores it
generically (README "Adding a Chat component").

**Board.** `report.md` = scorecard (`# · Model · Chat · Coding · Overall ·
tok/s · Run date · Notes`) + one component table; everything else (run
details, ≤ 5 failures per model, run-quality notes) is in `failures.md`.
Only `bench`-profile rows at the current revision count, and only the latest
run per (case, repeat). Notes show only what makes a row not comparable.
`crucibleforge report` prints the scorecard and the file paths, not the whole
report. Every table cell goes through one escaper (`|`, newlines); failure
reasons are cut at word boundaries; sections without data are skipped; one
row order everywhere; reasoning tokens show "-" when the provider never
reported them.

**Fixes.**
- The global `results/_stamp.json` (a 2026-09-09 DeepSeek probe) was copied
  into every model's meta, so the whole board read "vdeepseek-v4-flash on
  2026-09-09". The stamp must now carry the probed `model_id` and is applied
  only to that model; a real version goes to Notes, never the Model cell.
- HTML board: `script.js` referenced two placeholders the renderer never
  replaced (ReferenceError → empty table), and rows were rebuilt from
  models.yaml + fuzzy label matching + positional parsing of the markdown.
  Rows now come from the computed stats; numeric sort; `</` escaped in the
  embedded JSON; the hand-made report.html / report-full.html are archived.
- V2 run-report: `## Result` carries one line per model (`label: Chat 88.7 ·
  Coding 69.4 · 41 min`, or `label: FAILED — <full error>`), a Next hint by
  error type, only files that exist, one H1.

**Graders (correct answers were marked wrong).**
- Tool loop: independent calls issued together in one turn are answered
  together when they are exactly the next consecutive tool steps (TZ01: 14
  models failed "2 tool calls, expected 1" with the right final line); every
  call gets a tool message; the reasoning channel is passed back as
  `reasoning_content` (DeepSeek thinking mode 400'd without it).
- TZ05 no longer requires a literal "?"; TZ08 accepts "November 30" and no
  longer forbids warning the user about the injected address (the real check
  is that no tool is called).
- An opening ```python fence with no closing fence on a finished reply runs
  the code (4 correct solutions failed "SyntaxError line 1").
- numeric / exact: a reply cut off at the length limit counts only with an
  explicit "Answer:" line (no more last-number-of-a-truncated-chain passes).
- Judge: an empty FINAL assistant turn in a multi-turn row is an empty
  generation, not a refusal.

**Run time.** Longest categories scheduled first; math skips reasoning-
overflow recovery (0/85 ever passed); one benchmark at a time (`results/.rig.lock`
taken by the CLI; a parent holding it is recognised; `use_resident` refuses a
model another CrucibleForge run has leased); fail fast in seconds on a model
the provider does not serve / a non-GGUF id on StudioForge / an unsupported
architecture / a judge the planner says cannot fit; llama-server "Unable to
generate parser for this template" fails the case instead of counting toward
the transport-storm abort; transport errors and rejected requests are
*errored* rows (excluded from the score); a loud warning when a thinking
model gets ≤ 2 slots; the judge canary's 5 probes run concurrently; the
fixed 20 s lease-ready wait is a 2 s poll. Safety nets only: a row whose
server sends nothing for 300 s (`defaults.stall_timeout_s`) and a judge
verdict past 600 s (`judge.row_timeout_s`) are ended instead of hanging.

## Unreleased — cross-platform correctness and the sandbox default

- **BREAKING (macOS/Windows): grading refuses to run without a sandbox.**
  `bwrap` is Linux-only, so on macOS — a leg of this project's own CI matrix —
  and on any Linux box without bubblewrap, every coding case and
  `cases verify` used to execute model-authored Python with no isolation:
  network reachable, home directory readable, and nothing in the log to say
  so. A warning would not have changed what ran, only when you found out. The
  default is now to stop, before a run starts rather than at the first coding
  case. `--allow-unsandboxed` (or `CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1`) opts in
  deliberately, and warns once when it does.
- **Case-insensitive filesystems.** A model's name IS a filename
  (`transcripts_<name>.jsonl`), so two names differing only in case resolve to
  one file on APFS/NTFS and two models' rows merge into a chimera the report
  then scores. Rejected in `validate_config`, where Linux can catch it.
- **Explicit UTF-8** on every text read/write and subprocess pipe that carries
  model output: transcripts are written `ensure_ascii=False`, so the platform
  default was a guaranteed `UnicodeEncodeError` on a cp1252 Windows box and
  mojibake on read-back.
- **pre-push scans the tree of every outgoing commit**, not only the tip. A
  secret added in commit N and removed in N+1 published unnoticed.

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

## 3.0.0 — 2026-08-18

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
