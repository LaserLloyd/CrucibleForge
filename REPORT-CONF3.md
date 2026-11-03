# CONF-3 — Part 3 (gate + board + protocol stamp) of the CrucibleForge conformance plan

Worktree: the CONF-3 job worktree (branch `conf-gate`) · base commit
`ff7fbe1` ("CHANGELOG: conform is 4.0.4 (case lint 4.0.3)") = Parts 1+2.
Parts 1 and 2 were not rewritten: the only change inside Part-2 code is
additive (a `ctx`/`loaded` parameter on `run_model`, the fingerprint taken
after `adopt_rig_reasoning()`, and the new check `c7`).

## The four Part-3 requirements, one by one

### 1. Rows record `protocol`; a model whose rows mix fingerprints is told to re-run

- `crucibleforge/runner.py:280` `protocol_stamp()` — the protocol a row is
  measured under is the conformance fingerprint (provider type, reasoning
  replay, extra_body keys, sampler): the same shape the `conform` record
  carries, so a row can be compared with the record that says the protocol
  works for this model.
- `crucibleforge/runner.py:1129` — the fingerprint is computed once per load
  round; `crucibleforge/runner.py:1179` — every row carries it ("protocol").
- `crucibleforge/conformance.py:687-688` — the record's fingerprint is taken
  AFTER `ctx.adopt_rig_reasoning()`, so a rig model's record matches the
  rows it produced (the rig's `reasoning_format` is part of the request).
- `crucibleforge/report.py:903` `_protocol_mix_note()` — a model whose rows
  carry more than one fingerprint gets the note
  `"rows predate harness change <x> — re-run"`, where `<x>` names the
  fingerprint fields that moved (e.g. `reasoning_replay`,
  `reasoning_replay+extra_body_keys`). `report.py:888` `_is_api_model()`
  scopes it to API models (the plan's "(API models)"); `report.py:918`
  builds the string; `report.py:996-998` puts it in the board Notes.

### 2. `run` / `bench-all` require a conformance record for the CURRENT fingerprint

- `crucibleforge/conformance.py:766` `gate()` — a record for the current
  fingerprint lets the model run; a missing or stale one makes the checks run
  now; a FAIL blocks the model; `skip=True` (`--skip-conformance`) overrides
  it and the override is logged (`conformance.py:783`).
- `crucibleforge/conformance.py:742` `record_is_current()` (a SKIP record
  never counts: it proves nothing), `:755` `fail_reason()`
  (`conformance FAIL: <check> — <why>`), `:816` `gate_entries()`
  (a list of models; a rig model is left to the runner).
- `crucibleforge/cli.py:860` `_conformance_gate()` + `cli.py:934` — the
  gate runs before the generation phase of `run` / `all` / `bench-all`. A
  blocked model gets its meta written as FAILED with the reason (so the board
  and the run report say why) and is not run; the batch continues with the
  other models.
- `crucibleforge/runner.py:1139` — a RIG model is gated in the runner, right
  after the load (`loaded=True`), because a rig model can only be checked
  while it is loaded (`conformance.py:654` `run_model(..., loaded=True)`).
- `crucibleforge/cli.py:2478` (`run`/`all`) and `cli.py:2636`
  (`bench-all`) — the `--skip-conformance` flag;
  `crucibleforge/bench_all.py:901` passes it into the per-group run.

### 3. Board Notes + a report.md section

- `crucibleforge/report.py:875` `conformance_record()` reads
  `results/conformance/<label>.json`; `report.py:992` puts
  `conformance FAIL: <check>` (the failing checks) or the WARN summary
  (`conformance WARN: <ids>`, `report.py:994`) into the board Notes column;
  `report.py:1972` hands the label to `_coverage()`.
- `crucibleforge/report.py:2701` `_conformance_lines()` — a `## Conformance`
  section in `report.md` listing every model's verdict and the date it was
  proven on (the checks are listed only when one of them is not a PASS).

### 4. `docs` / `docs_verified` on a provider row

- `crucibleforge/conformance.py:596` — the new check `c7 docs`: a HOSTED
  provider (a public API, not a local server — `conformance.py:577`
  `_is_hosted()`) must carry `docs: <url>` and `docs_verified: YYYY-MM-DD`.
  A missing entry, an unparsable date or a review older than 120 days
  (`conformance.py:91` `DOCS_MAX_AGE_DAYS`) is a WARN: the "review the
  vendor docs" step becomes visible. A local provider is INFO (it has no
  vendor docs).
- `models.example.yaml:24-27` documents the two keys, `models.example.yaml:92-93`
  shows them on a provider row; `README.md:399` (the check table),
  `README.md:407` (the new "The gate" section) and `README.md:472` (the
  registry bullet) document them.
- `CHANGELOG.md:9` — the 4.0.5 entry.

## Tests

- New: `tests/test_conformance_gate_20261103.py` (the stamp, the gate's
  PASS / WARN / FAIL / skip paths, the rig gate, c7) and
  `tests/test_conformance_board_20261103.py` (the board notes, the
  mixed-protocol note, the report.md section).
- Every PASS/WARN/FAIL path runs against an HTTP-level fake provider
  (`httpx.Client` replaced), so the real request path is exercised and no
  network, no rig and no key is needed.
- Two legacy tests were adapted, not weakened:
  `tests/test_runner_stop_39.py` spawns the CLI against a host that HANGS —
  the gate would (correctly) refuse the model before the run starts, so that
  test now passes `--skip-conformance` (it tests the SIGTERM path);
  `tests/test_all_judges_survivors.py` monkeypatches `runner.run_models` with
  a lambda that now takes `**kw`.
- Final suite line (whole suite, `uv run --offline pytest -q -n 4 -o addopts=""
  --basetemp=.../tmp-conf3`): `1874 passed, 9 skipped, 365 warnings in
  1562.97s (0:26:02)`
- The suite was RED before this line and is GREEN now: an earlier run reported
  `3 failed, 1871 passed, 9 skipped, 365 warnings in 1576.39s (0:26:16)`. The
  three failures were ONE Part 3 defect, and it was a real regression, not
  contention: the three tests in `tests/test_simplify_20260923.py` failed with
  `AttributeError: '_RunProv' object has no attribute 'extra_body'` at
  `crucibleforge/conformance.py:239`, reached from the Part 3 stamp
  `crucibleforge/runner.py:1129` (`protocol_stamp` → `protocol_fingerprint`).
  Part 3 reads two `Provider` fields off the provider the runner holds —
  `extra_body` and `reasoning_replay` — and that suite's duck-typed `_RunProv`
  double carried neither, so every test reaching `_run_one_model` through it
  raised before the first row. It reproduces in 0.35 s in isolation: no rig,
  no network, no sandbox, no code-bank, so it is not load. The `Provider`
  dataclass declares both fields with defaults
  (`crucibleforge/providers.py:115` `extra_body`, `:146` `reasoning_replay`),
  so real providers were never affected — the double was out of date with the
  `Provider` contract. FIXED: the double now carries the two fields with the
  same defaults the dataclass declares.
- The Part 3 test files pass on their own:
  `tests/test_conformance_gate_20261103.py` + `tests/test_conformance_board_20261103.py`
  + `tests/test_conformance_20261102.py` = 54 passed.
- A first run of the same command on this box reported `5 errors` instead: a
  concurrent pytest (a different job, same `--basetemp` path) wiped the
  basetemp mid-run and deleted the xdist `popen-gw*` dirs, so five tests
  errored at setup with `FileNotFoundError`. That is an artefact of sharing
  a `--basetemp` with another run, not a property of this branch. The two
  tests involved (`tests/test_v3.py::test_openclaw_import_maps_env_keys_and_prices`,
  `tests/test_v3.py::test_profile_unknown_case_rejected`) pass in isolation
  (2 passed in 0.17 s) and pass in the runs above.

## Commits (in order)

1. `05ca2b3` runner: stamp every row with the request protocol it was measured under
2. `346a45e` run: gate the generation on a current conformance record (--skip-conformance overrides)
3. `3cd5610` report: conformance notes on the board, the mixed-protocol note and a report.md section
4. `303b881` conform: warn when a hosted provider's vendor docs are missing or stale

## Open issues

1. **The gate's probes use the provider's timeout** (900 s by default), so a
   host that HANGS blocks the gate for that long instead of failing fast. The
   pre-flight probe (with `--no-link-check` off) catches that case first;
   with the link check skipped the gate is the first thing to touch the host.
   Not fixed here: it needs a per-check timeout in `conformance._ask()`.
2. **The mixed-protocol note is scoped to API models**, following the plan's
   "(API models)". A rig model whose rows mix fingerprints gets no note —
   its board is rebuilt from the rig anyway, but the rows still exist.
3. **`--skip-conformance` is logged, not recorded.** The override appears in
   the log (and on the board only when a record exists); the meta does not
   carry a "gate skipped" flag, so a board built with the override looks like
   a board without one.
4. **The gate costs a round of cheap calls per model** (~20 requests) the
   first time a model runs under a new protocol. The record is cached in
   `results/conformance/<label>.json`, so it is paid once per protocol
   change, not once per run.
5. **No live smoke was run.** The MiniMax key was not used: the brief makes
   a live smoke optional and the rig lease is out of scope for this job. The
   whole gate was proven with the HTTP-level fake instead.
6. The `c7` check adds a WARN to every hosted provider whose row has no
   dated docs URL, so the first `conform` on a fresh registry reports a WARN
   (the model still runs — only a FAIL blocks).
7. **The three failures the suite carried before this report are FIXED**
   (see "Tests"): the Part 3 stamp read `extra_body` and `reasoning_replay`
   off the provider, and the duck-typed `_RunProv` double in
   `tests/test_simplify_20260923.py` had neither. The double now carries
   both with the defaults the `Provider` dataclass declares, and the whole
   suite is GREEN. The production path was never wrong — `Provider` always
   has the two fields — so no runtime behaviour changed.
