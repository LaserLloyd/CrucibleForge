# Bug-fix checker (`crucibleforge/fixcheck.py`)

This is the grader behind the "Fixes and changes existing code" segment: its rows reach the report through
`crucibleforge/fixrows45.py`. The bench45 standard composition runs its units (below); the report counts only rows
that carry its stamp.

## What it grades

Case CB-F1 (agent, tools) and the generated families `patchfix` and `patchmod` (one-shot replies).
Any other case raises `ValueError` (no probe spec). It replaces `codebank.grade_repo_tests` /
`codeenv.RepoEnv.grade` for these cases only; those functions are unchanged and still grade every
other code-bank case.

## Call

| Case | Call | Input |
|---|---|---|
| patchfix, patchmod | `fixcheck.grade_answer(reply_text, materialized["grader_config"], finish_reason=None)` | the model's reply (SEARCH/REPLACE blocks or whole files) |
| CB-F1 | `fixcheck.grade_agent(env)` | the finished `codeenv.RepoEnv` of the agent session (use it instead of `env.grade()`) |
| any tree | `fixcheck.grade_tree(tree, cfg, changed=True, extra=None)` | a `{path: text}` tree |

## What it returns

The `codebank.score_groups` dict, so the report code can read it the same way:
`score` (0..1, the row score), `grade` (`pass` when every group passed), `results` (one element per
group: `id`, `type` f2p/p2p/scope, `pass`, `detail`), `rate`, `detail`; plus `checker`
(`fixcheck-host-compared-1`, stamp it on the row), and for `grade_answer` `format_errors`, `applied`.
Score rule as before: passed f2p groups / f2p groups, halved when any p2p group fails, halved again
when the agent's scope element fails.

## Floors it guarantees (tests: `tests/test_fixcheck_kill_20261119.py`)

- The given (unfixed) program 0; a reply with no edits 0; a reply that rewrites files with the same
  bytes 0. The reference solution 1.0. A partial fix scores its share of the f2p groups.
- A reply that does not fix the bug scores 0 whatever it does at run time: reading the hidden tests,
  recognising a canary, exiting early with status 0, printing a success marker, writing or symlinking
  the result file, patching the driver, returning objects that compare equal to anything, forking a
  late writer, hanging, allocating without bound, using the network.
- Why: the expected values never enter the sandbox. Each unit runs in its own bwrap sandbox (no
  network, read-only system, 2 GiB address space, 20 s CPU, 64 MiB file size, 30 s wall clock) with a
  copy of the answer's tree and the unit's inputs only; the host compares what came out (strictly
  typed values) with the expected values it holds in memory.

## Things to know

- It refuses to run without a working bwrap (`graders.SandboxUnavailable`): without one the model's
  code could read the case files on the host. `--allow-unsandboxed` does not change that.
- Rows graded by it need their own stamp: `grader_stamp` does not cover the checker (deliberately, so existing
  families keep their stamps). The row stamp (`fixrows45.checker_stamp`) is a sha over `fixcheck.py`, `fixspecs.py` and
  the helper files that decide a verdict (`codebank.py`, `gen/_patchbase.py`, `graders.py`); a change to any of them
  makes every stored fix row stale. One-shot rows can be re-graded after a checker edit. Bug-hunt rows (CB-F1)
  cannot: the final tree of the agent's session is not stored, so after a checker edit they go pending until the
  unit is generated again.
- The CB-F1 probe spec (`fixspecs.py`) is pinned to the sha256 of CB-F1's hidden files; if the
  hidden tests change, grading raises instead of using a stale spec.
- Cost: one sandbox per unit (patch families: one per group, about 8-16; CB-F1: 18), 4 in parallel,
  a few seconds per row.

## Fix rows in a run (W3b)

A standard run (the default composition) sends the Fixes segment's 23 tasks to the model, 46 rows in all: the bug
hunt `CB-F1-larder-bughunt` (an agent session, asked 24 times), ten generated fix tasks (`PATCHMINI-core-1-1` to
`-3-2`, levels 1 to 3, and `PATCHFIX-core-1-1` to `-4-1`, levels 1 to 4) and twelve generated change tasks
(`PATCHMOD-core-3-1` to `-3-12`, level 3). The fix and change tasks are one-shot replies with a 65,536-token budget.
Fix tasks from level 5 up are in the Fixes pack (`c3_fix` set-3), not in the standard run. Each row
stores `row["fixcheck"]` = `fixrows45.stamp_verdict(...)` of the host-compared verdict (plus `cut` / `empty` /
`short_cut`); the Fixes segment reads nothing else. A one-shot reply cut at 98% or more of the budget sent scores 0
and stays in the mean (D10a); a reply cut below that is graded as delivered; an empty reply scores 0. The bug hunt
is graded on its final files. A box without a working bwrap stores `fixcheck: {unavailable: true}` (the row never counts; the segment reads "fix rows not graded: no sandbox").

Generate ONLY some of these units for a model into an existing result folder (the folder's stored rows are only
appended to; nothing is archived or re-judged; a unit that already has a reply is not drawn again, only one whose
attempt failed on infrastructure is), naming the case ids, for example:

    crucibleforge --results <result folder> run --profile bench45 --models <model label> \
        --cases CB-F1-larder-bughunt,PATCHFIX-core-1-1,PATCHMOD-core-3-1

Add `--spend-cap-usd N` for a hosted model (the rows count toward the cap like any other). Then
`crucibleforge --results <folder> report --profile bench45` prints the Fixes points. To draw a unit again on purpose,
archive its rows first; a later attempt is ignored at report time otherwise (`attempts45` in report.json).
