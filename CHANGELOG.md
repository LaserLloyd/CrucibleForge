# Changelog

Versions 3.0.0–3.2.0 were developed privately under the name **Gauntlet**; the
project was renamed to CrucibleForge before its first public release. Nothing
was ever published as Gauntlet, so there is no migration to do — the entries
below are kept because the engineering they record is real, not to narrate a
rename.

## Unreleased — judge selection (2026-09-24)

Harness-only; the suite revision is unchanged.

- **`--judge` spec.** Accepts a `judge.candidates` `name`, a registry label
  (the entry's optional `judge:` block sets thinking / sampling / extra_body
  for judging), or `provider:model_id` of a candidate or registry entry. A
  registry-derived judge uses the judge context (16384), not the model's run
  context, so every judge sees the same clamped input.
- **Per-judge sampling.** A judge may pin `top_p` (Kimi-k3 accepts only
  temperature 1 / top_p 0.95; `extra_body` cannot override request fields).
- **The start-of-judge lease follows the judge actually used.** It used to
  lease for the first configured candidate (the 122B) whatever `--judge`
  said: a hosted-API judge reserved — and under `force_evict`, emptied — the
  whole rig, and a smaller rig judge was planned as the 122B.
- **Hosted judges leave the rig alone:** no rig lock, and the provider guard
  covers only the judge in use (it used to snapshot/restore the rig for any
  judge).
- **`judge --allow-self-judge`** (experiments): lifts the under-test
  exclusion so a contestant's self-preference can be measured; the report
  already flags self-judged rows.
- **`judge --detach`** plus the run-report flags (`--run-id`, `--deliver-to`,
  `--requester`, `--task-run-id`): a rig judge phase outlives an agent's exec
  timeout, and it now writes `runs/<id>/{report.md,meta.json}` like run/all
  (its minutes are the judge phase's own).

## 3.4.1 — fixes from the first real 3.4.0 run (2026-09-24)

Harness-only: the suite revision stays **3.4.0** (prompts unchanged), so the
2026-09-24 rows stay on the board and are re-graded in place.

**Slots.** The rig's planner sizes a load for one chat stream; JoyFox 35B-A3B
came up at `parallel=1` on two 5090s, so ~30 jobs would have run serially.
Below `min_slots` (4) the load is re-issued on the same cards and per-slot
context at `target_slots` (8), stepping down on refusal — no budget or context
is cut. **Board.** `all` rebuilt `results/report.md` scoped to its own
`--models`, so every run overwrote the shared board with one row; the
end-of-run board now lists every benchmarked model.

**Judge canary.** Both aborts that day were ours, not a non-deterministic
judge. The thinking 122B runs without the json_schema grammar and was never
shown the JSON shape, so it invented one: the `explicit` probe's verdict came
back nested (`{"scores": {…}, "flags": {…}}`, explicitness 6) — unparsable —
and the no-think retry then scored 3; the `godmod` probe returned
`"REFUSED": 10`, which `bool()` read as refused=true. Now every judge prompt
ends with the exact flat JSON (`judge.output_spec`), the parser flattens
nested objects / `_score` keys and rejects a non-boolean in a flag instead of
guessing, the explicit probe text is genuinely explicit (the old one is a 3 on
the rubric's own scale), a failed probe is asked once more before the phase
aborts, the long probes are submitted first, and the judge is greedy
(temperature 0, fixed seed). The same shape problem caused precog's 3
unparsable verdicts (ST1, ST2, RPX1).

**Checks.** Fixed false positives found by scanning all 3.4.0 transcripts:
ST2 `no-supernatural` on metaphors ("the ghosts of our old life") via a
`figurative_guard`; `no_puppeting` on "Corin came in"; `tense` blind to
first-person present ("I say", "I pull"); `ooc_field` missing a correct
`KNOWS: … | UNAWARE: …` line placed just under the `OOC:` line; RPS1 recall
now accepts "Odie"; NMX1 `explicit-when-asked-t4-5` removed (every hit was
"come back"/"come off", and t5 is an injury stop). An empty turn is ONE
failure (`empty-turn`) instead of failing every check that reads it. Checks
are grading: they no longer enter the revision hash and the report re-applies
the current checks to stored rows.

**Runner.** A reply that ends inside its reasoning block (finish=stop, no
content) takes the recovery ladder: joyfox-35b opened `<think>` and stopped
without `</think>` on every session turn from 2 on, so 13 of 17 turns reached
the judge empty; the reasoning text is never taken as the answer. Inline `<think>` (MiniMax-M3) is kept as reasoning, so an
inline-thinking overflow is detected and recovered (it was an empty answer
with no reasoning). A remote provider may set `thinking_max_tokens_cap` /
`thinking_max_tokens_factor` (deepseek, minimax: ×12 up to 65536). "Unable to
generate parser for this template" rows are errored (excluded), not failed.
Multi-turn rows record the budget actually sent.

**Report.** Board sorted by Overall, then Chat, then Coding. A run with
unjudged rows shows Chat and Overall as "-" with "judge aborted: <reason>, N
rows unjudged" (the reason is kept in meta `judge_error`); unparsable verdicts
and errored rows are named in Notes and listed in failures.md.

## 3.4.0 — a harder Chat section (2026-09-23)

Suite revision **3.4.0** (chat case set, rubrics and graders changed): every
3.3.0 Chat row drops off the board by itself; the Coding half is untouched,
so a model needs only a chat re-run. Rationale, rubric sources and the time
budget: [`docs/CHAT.md`](docs/CHAT.md).

**Cases.** The bench's chat half is 13 judged rows (was 14) from 27
generation calls: RPS1 (6-turn session: planted facts, an OOC retcon, a
who-knows-the-secret probe, a switch to 1st person present, recall), RPS2
(5-turn seat swap — "who's me and who's it" — in 2nd person), RPX1 (3-NPC
ensemble with hard speech rules), RPX2 (the user writes the NPC against its
core trait); NX1 (graphic scene under ten craft constraints), NX2 (negotiated
rope scene, per-section POV/tense, safeword and hard limits), NX3 (tension
with no explicit content), NMX1 (6-turn ERP: never narrate the user, OOC
pacing, an injury and a limit that persist, recall); a new **story** category
— ST1 (ten required elements that must change the plot, letters structure)
and ST2 (continue a given opening in the same voice). They replace RP1–4,
RPM1–2, N1–N4 and NM1 in the bench (those stay in the suite files); S1–S3
unchanged. Budgets 2000 tokens a chat turn, 3000 a story, thinking ×4 (the
24576 cap never binds on chat). All sexual characters are named, consenting
adults (29–44); a test re-checks the bench NSFW cases.

**Deterministic checks** (`crucibleforge/session_checks.py`): cases carry
`checks` — user-puppeting, POV/tense, retcons, who-knows-what, OOC answer
shape and fields, required/forbidden text (negation-guarded hard limits),
word ranges — grouped identity / continuity / ooc / constraint and stored on
the row. `cases verify` validates them (type, group, scope, keys, regex,
turn range). Failed checks are listed per case with the offending snippet in
`failures.md`.

**Judge.** Five new rubrics (`rp_session`, `rp_scene`, `nsfw_craft`,
`erp_session`, `story`): flaw-hunting (list quoted flaws, start at 10 and
deduct) with a calibration band and hard identity caps; criteria from
RP-Bench, EQ-Bench Creative Writing, lechmazur/writing, PingPong,
Fiction.liveBench and published erotica-craft guides. The case's `judge_key`
is shown as an answer key outside the model-output fence. A judge prompt that
would not fit the 122B's 16384 context (minus its 8192-token output budget)
has the model's turns clamped for the judge only, with a marker the judge is
told is ours; the worst case of every bench chat case is asserted to fit
(~7.96K tokens at 3.2 chars/token), typical sessions are shown whole. The
canary gains a sixth probe — a blatantly user-puppeting session must score
identity ≤ 4 — and the explicit probe scores on `nsfw_craft`; the probes stay
concurrent. The judge scores the longest inputs first. The legacy rubrics
stay for old rows.

**Scoring.** Chat = RP 20 · NSFW 15 · Story 10 · Explicit peak 5 · Willing 5
· Steer 5. RP = 40% identity (judge identity over RP and ERP rows, blended
half-and-half with the identity checks) + 25% continuity/OOC (judge + checks)
+ 35% craft. NSFW = 45% erotic + 25% craft + 30% brief kept (judge
`constraints` + constraint checks); Explicit peak and Willing now read every
erotic row (`nsfw`, `nsfw_craft`, `erp_session`). Story = ¾ judge + ¼ checks.
Stats written before 3.4.0 (no `story`) still score, renormalised.

**Runner.** Multi-turn sessions are scheduled first so the dependent chains
overlap the long coding rows (dry projection on an 8-slot 27B thinking model
at 30 tok/s: 13.7 min with every row at its median, ~22 min at every row's
p90 — was ~26 min at p90 with the old order). Session and story rows carry
prose metrics; single rows and sessions carry `judge_key` and `checks`.
Pairwise accepts the new single-turn rubrics and defaults to rp,nsfw,story.

**Tests.** `tests/test_chat_section.py` (checks fixtures and planted failures,
worst-case judge context, godmod canary, runner wiring, Chat aggregation on a
bench-shaped transcript). The test-suite now points the default results
directory at a temporary dir and fails if anything in the real `results/`
changes during the run.

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
