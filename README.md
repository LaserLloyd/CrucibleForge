# CrucibleForge / MakoBench

[![CI](https://github.com/LaserLloyd/CrucibleForge/actions/workflows/ci.yml/badge.svg)](https://github.com/LaserLloyd/CrucibleForge/actions/workflows/ci.yml)
[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-green.svg)](LICENSE)

**MakoBench 4.5 is a low-cost benchmark anyone can run on a model they host or rent.** CrucibleForge is the engine.
It scores Code (write, fix and change code, use tools, read code) and Chat (stay in character, remember the
conversation, co-write to a brief, follow everyday instructions), each out of 10. It sorts models into broad tiers:
two single runs closer than about 0.45 Overall points cannot be told apart, and the board marks ties itself. NSFW+ /20 is shown apart, beside Overall and never part of it.

It is the engine behind the public [MakoBench board](https://www.laserlloyd.com/makobench/). <!-- scrub-ok: public project/site URL, deliberate -->
Current suite: **4.5.1** (`SUITE_VERSION` in `crucibleforge/version.py`), profile `bench45`.
Licence: [MIT-0](LICENSE). Author: Laser Lloyd.

<p align="center"><img src="docs/images/how-it-works.svg" alt="How MakoBench works: the same questions for every model, your model answers, the answers are checked by code and one local judge, scores with a range, the board" width="560"></p>

## First run in five minutes

The model under test and the judge are chosen separately, and each can be local or hosted: a laptop running a local
8B model can be judged through OpenRouter, a GPU box can run both, a plain computer can use OpenRouter for both.
The quick setup below needs only an [OpenRouter](https://openrouter.ai/) key and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge && uv sync --python 3.13   # scrub-ok: public repo URL
uv run crucibleforge config --init      # writes models.yaml; its `openrouter` provider reads OPENROUTER_API_KEY
export OPENROUTER_API_KEY=sk-or-...     # keys live in the environment, never in models.yaml
uv run crucibleforge models add gpt-6-luna openrouter openai/gpt-6-luna --context-length 1050000 --price-in 0.1 --price-out 0.5
uv run crucibleforge all --models gpt-6-luna --judge openrouter:qwen/qwen3.8-27b --plan   # calls nothing
uv run crucibleforge all --models gpt-6-luna --judge openrouter:qwen/qwen3.8-27b --spend-cap-usd 6
```

- **Prices** are USD per million tokens from the model's OpenRouter page; a hosted model without one is refused.
  `--plan` prints the rows, the minutes and three price figures (a typical run, a model that thinks at length,
  every row to its budget), then the judge's estimated cost on its own line. Set the cap to about twice the figure
  that fits the model.
- **The judge** here is Qwen 3.8 27B through OpenRouter. Any judge works, local or hosted, any model:
  `crucibleforge all --models <label> --judge <provider>:<judge-model-id>` (since 4.5.1). The scores published on
  MakoBench are judged by the same model run locally at 8-bit (Q8). A hosted judge is billed by its provider, is
  not part of the model's Run cost and is not limited by the run's spend cap. A hosted judge receives every reply it
  judges, NSFW+ included; pass `--no-nsfw-plus` to leave NSFW+ out. `--no-judge` scores what code
  checks and leaves the judged parts `pending`.
- **Local instead.** A local model under test: `uv run crucibleforge models add my-8b lmstudio <model key>` (no key,
  no price); a local judge: load it in LM Studio and pass `--judge lmstudio:<model key>`. Any OpenAI-compatible
  server works ([docs/CONFIG.md](docs/CONFIG.md#providers)).
- **NSFW+** (adult fiction, NSFW+ /20) runs by default in a standard run and never enters Overall.

While it runs you see one `[gpt-6-luna] <case> r1 done (N rows)` line per answer, a run summary with the cost, the
judge's lines, and finally `wrote results/report.md` and `results/report.html`. On the board, **Overall /20** is
Code plus Chat and sets the rank; **Code /10** is code that hidden tests run (write, fix, use tools, read code);
**Chat /10** is role-play, memory, co-writing and everyday instructions, checked by code and the judge; `±` is the
90% range of one run. The same steps in Docker: [docs/FIRST-RUN-DOCKER.md](docs/FIRST-RUN-DOCKER.md).

<p align="center"><img src="docs/images/board.png" alt="The generated 4.5 board: a table of 11 ranked models with columns rank, Model, Overall out of 20, Code out of 10 and Chat out of 10, each score with its plus-minus range, tied ranks such as 1= and 3=, and bonus pack points printed apart as not in Overall" width="720"></p>

- `--smoke` runs a small subset to prove your setup; it is not a score. A point-and-click version: `uv run crucibleforge gui`.
- On Windows and macOS add `--allow-unsandboxed` before the subcommand, or use Docker (coding answers run in a
  sandbox those systems lack).
- **Cost of other models.** A frontier model can cost much more than a flash-class one, and its bonus packs (run
  only at 90% on a part) can cost more than the standard test. A local model is free but slow when it thinks at
  length (a 27B thinking model took 269 to 432 minutes with judge and packs on two GPUs).

## The scores

Code and Chat are each worth 10 points; **Overall /20 is their sum**. The weights are read from the profile by
`tests/test_readme_matches_profile_45.py`.

<!-- parts:begin -->
| Section | Segment | Points | What it asks |
| --- | --- | --- | --- |
| Code | Writes working code | 4 | Write small programs from 18 briefs that leave obvious details unsaid (19 functions in all); hidden tests check them |
| Code | Fixes and changes existing code | 3 | Find and fix bugs in a small program, or change it, without breaking what works |
| Code | Uses tools | 2 | Finish three scripted tool dialogues by the rules, step by step (1 point), and 30 seeded tool tasks (1 point) |
| Code | Reads code | 1 | Say what a short program prints, including one for a made-up machine |
| Chat | Stays in character | 4 | Hold a role-play scene under rules the user keeps pushing, and two long scenes |
| Chat | Remembers the conversation | 3.5 | Bring back earlier facts, keep a ledger and work out totals |
| Chat | Co-writes to a brief | 1.5 | Write to a brief with stated constraints and required elements |
| Chat | Everyday instructions | 1 | Small checked tasks (format, word problems, a document) and saying NOT STATED when the document does not say |
<!-- parts:end -->

<p align="center"><img src="docs/images/score.svg" alt="Code is 10 points (Writes 4, Fixes 3, Tools 2, Reads 1), Chat is 10 points (Character 4, Memory 3.5, Co-write 1.5, Everyday 1); together they are Overall out of 20" width="560"></p>

**NSFW+ /20, shown apart.** NSFW /10 (Erotica 4, Adult role-play 2.5, Range 2, Anatomy 1.5) plus Uncensored /10
(Willingness 6, Knowledge 4), printed in its own column beside Overall and never part of it; both halves are
printed too. Points come from checks and the judge's classing, never from taste or writing quality.

Every segment with its rows and grader: [docs/WHAT-IS-TESTED.md](docs/WHAT-IS-TESTED.md). Every question, one line
each: [docs/TESTS-BY-COMPONENT.md](docs/TESTS-BY-COMPONENT.md). The rules for refusals, empty and cut replies:
[docs/SCORING.md](docs/SCORING.md) and [docs/SCORING-RULES-45.md](docs/SCORING-RULES-45.md).

## Bonus packs

A part scored at 90% or more opens a harder bonus pack. Each pack is worth up to 1 bonus point. Bonus points are
shown apart, as hashed bars after the solid score, and never count in Overall /20. How packs work and how to write
one: [docs/PACKS.md](docs/PACKS.md).

<p align="center"><img src="docs/images/packs.svg" alt="Bonus packs: a part at 90 percent or more opens its pack of harder questions; each pack is worth up to one bonus point; bonus points are drawn as hashed bars after the solid standard score and never count in Overall" width="560"></p>

## Methodology

- **Fixed questions.** Every model gets the same question sets. Generated items come from the release seed `4.5.0`
  and fixed card seeds, so two runs ask the same questions.
- **Same conditions.** Each model runs with its maker's recommended sampler, its default thinking and the same
  output budgets. The first reply a unit produced is the one scored; a retry counts only after an infrastructure
  failure, or when 4.5.1 owes the unit again (a reply cut under 4.5.0's lower budget).
- **Code checks first.** Code is run in a sandbox, answers are compared with keys written in advance, and rules are
  checked by code. Only what code cannot check goes to the judge.
- **One judge.** On the published board the plain Qwen 3.8 27B, run locally at 8-bit (Q8), grades the role-play
  sessions, the long chat scenes and the NSFW+ classing, with a quoted line as evidence. There is no second reader.
- **Ranges from repeat runs.** The `±` beside a score is a 90% range measured from whole runs repeated on the same
  models. One run: Code ±0.22, Chat ±0.29, Overall ±0.31. NSFW+ needs two runs to get under 5%.
- **Ties.** Rank is by Overall. A model is marked tied with the one above (`2=`) when neither its Code total nor
  its Chat total differs from that model's by more than the 90% margin of the difference.
- **Packs.** A part scored at 90% or more unlocks a harder bonus pack. Pack points are printed apart and never
  enter Overall. List them with `crucibleforge pack list`; how they work: [docs/PACKS.md](docs/PACKS.md).
- **Published per run.** Each score with its range and source, run settings, minutes and cost. Every row stores
  its grader and judge stamps, so a changed grader marks old rows stale (`report --check-stale`).

How the benchmark itself was checked (repeat runs, judge audit, kill tests, reviews): [docs/VALIDATION.md](docs/VALIDATION.md).

## Reading the board

| You see | It means |
| --- | --- |
| **Overall /20** | Code plus Chat, printed only when both are complete; otherwise `x of y measured, not ranked`. |
| `6.4 of 7.5` | Points measured out of the measured maximum; nothing is scaled up over a missing segment. |
| `±` | The 90% range, with its source beside it. |
| `2=` | Tied with the model above. |
| **Pack points** | Bonus packs, apart from the 20. `locked` = the part is under 90%. |
| `pending` / `not run` | Waiting for something / never run (never 0). |
| `not measured (n of m replies cut)` | Too many replies hit the token ceiling; the part adds nothing. |

Every column and mark: [docs/SCORING.md](docs/SCORING.md#board-columns).

**The judge.** `--judge <provider>:<model-id>` takes any model, local or hosted. The scores published on MakoBench
are judged by Qwen 3.8 27B run locally at 8-bit (Q8). Rows judged by another judge are scored and ranked on your
board and marked ⚖ with the judge's id. `--no-judge` scores what code can grade and leaves the judged
segments pending. More: [docs/JUDGE.md](docs/JUDGE.md).

## What it does not measure

General question answering, repository-scale coding or long agent loops, long-document work beyond a small check,
summarising, translation, vision and voice, and the top of the frontier, where the standard test is at its ceiling.
It grades behaviour, never a refusal as a virtue. Who it suits: [docs/SCORING.md](docs/SCORING.md#who-it-is-for).

## Install

CrucibleForge runs from a git checkout with `uv sync`. A wheel or `pip install` is not supported: the cases,
profiles and `models.example.yaml` live outside the Python package. Python 3.12 or newer works; the commands above
use 3.13, the version the Docker image uses. Coding answers run in a `bubblewrap` sandbox, so on Linux install
`bwrap`. Step by step for each system: [docs/INSTALL.md](docs/INSTALL.md).

The 4.1 test is frozen under `archive/4.1.0/` and runs with `--profile bench`; its rows do not mix with 4.5.

## Docs

| Doc | What |
| --- | --- |
| [docs/INSTALL.md](docs/INSTALL.md) | Windows, Linux and macOS, Docker or native, upgrade and uninstall |
| [docs/RUNNING.md](docs/RUNNING.md) | Running a model, the pipeline stages, the conformance gate, spend caps, the GUI |
| [docs/CONFIG.md](docs/CONFIG.md) | Providers and backends, `models.yaml`, hosted APIs and prices |
| [docs/CLI.md](docs/CLI.md) | Every command and flag, grouped by task |
| [docs/WHAT-IS-TESTED.md](docs/WHAT-IS-TESTED.md) | Every segment and pack: what it asks, rows, how it is graded |
| [docs/TESTS-BY-COMPONENT.md](docs/TESTS-BY-COMPONENT.md) | Every question in the standard run, one line each |
| [docs/SCORING.md](docs/SCORING.md) | The score tree, the judge, ranges and ties, every board column, known limits |
| [docs/SCORING-RULES-45.md](docs/SCORING-RULES-45.md) | What each outcome scores (refused, softened, filtered, cut, empty), and the marks files |
| [docs/VALIDATION.md](docs/VALIDATION.md) | How 4.5 was validated: repeat runs, the range method, judge audit, kill tests, reviews |
| [docs/PACKS.md](docs/PACKS.md) | The bonus packs and how to write the next one |
| [docs/DESIGN.md](docs/DESIGN.md) | Thinking models, failure handling, the sandbox, GPU leases |
| [docs/CASES.md](docs/CASES.md), [docs/CUSTOM-TESTS.md](docs/CUSTOM-TESTS.md) | Adding cases, your own private tests |
| [docs/CONTENT-RULES.md](docs/CONTENT-RULES.md) | What new tests never include |
| [docs/DEVELOPING.md](docs/DEVELOPING.md) | Repository layout, tests, the publishing scrub check |
| [docs/FIRST-RUN-DOCKER.md](docs/FIRST-RUN-DOCKER.md), [docs/DOCKER.md](docs/DOCKER.md), [docs/deploy/docker.md](docs/deploy/docker.md) | The container image: first run, quick setup and the always-on service |
| [docs/OPENCLAW.md](docs/OPENCLAW.md) | Driving CrucibleForge from an agent framework |
| [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md) | Release history, reporting a vulnerability |

On the site: [How MakoBench works](https://www.laserlloyd.com/makobench/docs/how-makobench-works/) and <!-- scrub-ok: public project/site URL, deliberate -->
[Run CrucibleForge on your own models](https://www.laserlloyd.com/makobench/docs/run-crucibleforge-on-your-own-models/). <!-- scrub-ok: public project/site URL, deliberate -->

## Content warning

The benchmark measures adult-fiction ability and how a model handles pressure, so some case files and the NSFW+
generators hold material you may not want to read. Every participant is an adult; the limits are in
[docs/CONTENT-RULES.md](docs/CONTENT-RULES.md). Model outputs (`results/`) never enter the repository. Leave NSFW+
out with `--no-nsfw-plus`, or skip categories with `--categories`.

## Licence

MIT No Attribution ([MIT-0](LICENSE)). You do not need to credit this project, but a link back is appreciated.
Releases up to 3.7.0 were MIT. The embedded board font is under the SIL Open Font License.
