# Developing and publishing

[README](../README.md) · [Install](INSTALL.md) · [Running](RUNNING.md) · [Config](CONFIG.md) · [Scoring](SCORING.md) · [CLI](CLI.md) · [Design notes](DESIGN.md) · [Cases](CASES.md) · [Developing](DEVELOPING.md)

## Layout

```
crucibleforge/       the package
  cli.py             commands and the run / judge / report pipeline
  runner.py          generation: parallel jobs, budgets, escalation
  graders.py         deterministic graders (exact, contains, python_exec, tool scripts)
  judge.py           the judge: canary, samples, quote verifier, verdicts
  report.py          the board: scores, margins, html / md / json
  bench_all.py       whole-board refresh and the Incomplete check
  ladder.py          bonus packs and their unlock rule
  conformance.py     the seven request-protocol checks and the gate
  preflight.py       runnable, link and credit checks
  providers.py       openai, lmstudio and studioforge provider types
  studioforge.py     planner loads and GPU leases
  config.py, profiles.py, version.py   registry, profiles, suite stamp
  gui/, templates/board/               web GUI, single-file board
cases/*.json         the case files (perf rpdeep nsfw coding codebank codeagent
                     tooluse instruct reasoning math steer rp story overrefusal
                     longctx planning, plus the long code tiers); cases/rp-deep/
                     holds the Chat session cards; cases/ladder/ the bonus packs
tests/               pytest, offline, includes full case verification
models.example.yaml  registry template (copy to models.yaml, which is git-ignored)
results/             outputs (git-ignored)
profiles/bench.yaml  THE benchmark: case selection, budgets, judge, scoring weights
scripts/             scrub_check.py + hooks/ (see Publishing), queue-overnight.sh,
                     docker-smoke.sh, case-bank build scripts
docs/                this documentation
CHANGELOG.md         release history (latest: 4.1.0)
```

`uv run pytest` needs no network. `uv run crucibleforge cases list` and `cases verify` check the case set.

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
