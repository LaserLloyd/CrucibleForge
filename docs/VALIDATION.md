# How 4.5 was validated

[README](../README.md) · [What is tested](WHAT-IS-TESTED.md) · [Scoring](SCORING.md) · [Scoring rules](SCORING-RULES-45.md)

This page lists the checks that were run on the benchmark itself before the 4.5.0 release, and the method behind
the `±` ranges on the board. It says what was not done, too.

## 1. Repeat runs

Every `±` on the board comes from whole runs repeated on the same model. All runs used the same question sets,
the same sampler settings and the one local judge.

| Model | Full runs | NSFW+ runs |
| --- | --- | --- |
| Reference model (plain Qwen 3.8 27B at 8-bit, local; also the judge) | 3 | 7 |
| Hosted flash-class model A | 2 | 2 |
| Hosted flash-class model B | 2 | 2 |
| Two more local models | 1 each | 1 each (a second run was being added at release) |

The reference model's three full runs:

| Run | Code /10 | Chat /10 | Overall /20 |
| --- | --- | --- | --- |
| 1 | 8.66 | 8.29 | 16.96 |
| 2 | 8.55 | 8.02 | 16.58 |
| 3 | 8.74 | 8.10 | 16.83 |

## 2. How the ranges are worked out

- For each model with repeat runs, take the standard deviation (SD) of its whole-run scores.
- Pool those SDs across the models, each weighted by its runs minus one.
- Range = t x SD / sqrt(runs shown). The board shows the mean of a model's runs, so two runs give a narrower range
  than one.
- t is the one-sided 95% Student t value for the degrees of freedom behind the SD. That makes the range a 90%
  range. The values used: 2.132 at 4, 1.943 at 6 and 1.860 at 8 degrees of freedom.
- From five runs on, a model uses its own spread instead of the pooled one.

The code is `crucibleforge/accuracy45.py`.

Pooled SDs on the board's scale:

| Score | Pooled SD | Degrees of freedom |
| --- | --- | --- |
| Code /10 | 0.103 | 4 |
| Chat /10 | 0.134 | 4 |
| Overall /20 | 0.146 | 4 |
| NSFW /10 | 0.539 | 8 |
| Uncensored /10 | 0.303 | 8 |
| NSFW+ /20 | 0.706 | 8 |

The resulting 90% ranges:

| Score | One run | Mean of two runs |
| --- | --- | --- |
| Code /10 | ±0.22 (2.2%) | ±0.16 |
| Chat /10 | ±0.29 (2.9%) | ±0.20 |
| Overall /20 | ±0.31 (1.6%) | ±0.22 |
| NSFW /10 | ±1.00 (10.0%) | ±0.71 |
| Uncensored /10 | ±0.56 (5.6%) | ±0.40 |
| NSFW+ /20 | ±1.31 (6.6%) | ±0.93 |

The mean of the reference model's seven NSFW+ runs has NSFW+ ±0.60, NSFW ±0.45 and Uncensored ±0.26.

**The target** is a range of about 2.5% of the score's maximum, and 5% at most.

- Code and Overall meet 2.5% with one run.
- Chat is under 5% with one run and under 2.5% with two.
- NSFW+ and Uncensored need two runs to get under 5%.
- NSFW alone stays above 5% with two runs; the reference model's seven runs bring it under.

The board prints the range and its source in every cell, and says so when a range is above 5%.

**Limits of the method.**
- Few degrees of freedom (4 for Code and Chat, 8 for NSFW+). The t values widen the ranges for that.
- The pooled spread assumes every model is about as noisy as the models that were repeated.
- A different set of questions of the same kind is a separate uncertainty. It is not in the `±`; it is printed as
  information and it is what the rank rule uses (on the totals, with each model's pack noise added) ([Margins and ties](SCORING.md#margins-and-ties)).

## 3. Judge audit

One local judge grades everything judged. A one-time audit checked it on the eight long chat scenes, which carry
4.5 of the 10 Chat points.

| Item | Result |
| --- | --- |
| Units | All eight scenes for five models, and both runs of the two models that had two runs at the time: 56 scene rows, 581 facts |
| How | Three other models judged the same facts from the same messages. Where two agreed, that was the truth; where they differed, the third decided. The rule was fixed before any of their answers was read. |
| Agreement | The first two auditors agreed on 503 of 581 facts (86.6%) |
| The judge against the truth | Differed on 35 facts: 18 too lenient, 17 too strict |
| Lean per model | Under 2.5% of Chat for every model; largest 1.6%, lenient |
| Widest case | Lenient on the co-written pieces of the two models that write most freely; for those two the 90% range of the lean reaches past 5% of Chat |
| NSFW+ rows | Never sent to any other model |

The lean is printed as information and is not corrected for.

**Not run:** the second audit pass, on the judged half of the long role-play sessions (1.5 of the Chat points).

## 4. Kill tests and stored-row checks

These checks change something on purpose, or re-render stored results, and confirm that the scores do not move.

| Check | What was done | Result |
| --- | --- | --- |
| Re-render and diff | Nine run folders and the board rendered by the code before and after a change, offline, with no model calls | Every segment point, total, unit count and mark verdict identical. Only ranges and margins moved, largest 0.25 points. |
| Pack removal | Nine pack set files deleted, three folders re-rendered | No standard cell, point, margin, range or Overall moved. Only pack entries changed. |
| Final re-render | After the last fix pass, every stored score in all ten run folders re-rendered (the nine above and one partial run of a sixth model, which is not ranked) | Matched the earlier render to four decimals, pack points included |

## 5. Independent reviews and the fix pass

Two independent reviews read the release candidate. Their findings were fixed in a single pass.

| Review | Scope | Main findings |
| --- | --- | --- |
| Scoring and report | Code, Chat, NSFW+, the ranges, the board | Ranges were read from a stale stored estimate instead of the runs. NSFW, Uncensored and NSFW+ had no range. Pack cells and notes were wrong once repeat data existed. Two failed NSFW+ checks were shown as "cannot be tested yet". |
| Run pipeline | Runs, hosted transports, the release script | A keep-alive cut meant for one provider applied to all. Two tests were unstable. |

All were fixed without moving a stored score (see the re-render in section 4). Smaller pipeline items from the
second review were left for 4.5.1.

## 6. Software tests

| Check | What it does |
| --- | --- |
| Unit suite | About 8,500 tests. A full run during the fix pass had 8,380 passed, 55 skipped and 0 failed. |
| Clean-clone walk-through | A fresh clone in a clean environment. Every command after `uv sync` ran with networking cut off: `config --init`, `--help` for each command, `all --plan` (279 rows, nothing called), `cases list`, `pack list`, `report`, `report --check-stale`, and `registry-check` (fails closed, as documented). Real runs, `--smoke` and the GUI need a model server and were not part of it. |
| Release scrub | `scripts/scrub_check.py` scans files for private strings: names, paths, hosts, keys. A commit hook runs it on every commit and says when only its generic rules were loaded. |

## 7. Known gaps

- The second judge-audit pass (section 3) was not run.
- Two ranked models on the launch board have a single full run and a third model has only a partial run, so their
  ranges are the pooled ones.
- The board's Minutes column is not recorded when a model's rows come from several invocations.
- The scoring rules were changed after the launch data had been seen. The release notes say what changed and why:
  [CHANGELOG.md](../CHANGELOG.md#450-release-notes-what-changed-since-41).
