# CrucibleForge — Chat & Coding

Generated <time> · suite 4.1.0+04da7abe · judge gemma-4-31B-it-uncensored…

## Scorecard

| # | Model | Overall | Class | Lean | Room | Coding | Chat | NSFW (separate) | Efficiency | tok/s | Run cost | Suite | Run date | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| - | deepseek-r1-0528-8b | - | - | - | = | **0**.0 ± 0.1 | - | - | 100% | - | — | 4.0.1 | 2026-11-03 | partial (1/33 cases); 1/1 rows errored (counted failed; a resume re-runs them) |
| - | gpt-6-1-terra | - | - | - | = | - | **2**.3 ± 0.1 | - | 100% | - | $0.01 | 4.0.1 | 2026-11-06 | partial (1/33 cases) |
| - | qwen2.5-vl-7b-caption | - | - | - | = | - | **0**.4 ± 0.1 | - | 100% | - | — | 4.0.1 | 2026-11-02 | partial (1/33 cases); 1 verdict judged under older grading (re-judge pending) |
| - | claude-haiku-4-5 | - | - | - | = | - | - | **2**.0 ± 0.1 · Refuses 0/1 | 100% | - | $0.00 | 4.0.1 | 2026-11-06 | partial (1/33 cases); near ladder set 1 of nsfw/uncensored (unlocks in 70% of resamples) |
| - | kimi-k3 | - | - | - | = | - | - | - | 100% | - | $0.00 | 4.1.0 | 2026-11-08 | partial (1/33 cases); 1 row unjudged; 1 reply blocked by the provider's content filter (a blocked reply scores zero); 1 of them in NSFW (counted as refusals) |

*Every category is out of 10 points on the standard test; ladder packs (harder questions, usually 3, worth 1 point per pack together, given only after ≥ 90% on everything below) are the only way past 10. Overall = Coding + Chat (each out of 10 on the standard test, max 20). NSFW is shown beside it, never added. The bold number is the main one; ± = half the 90 % interval of a two-level bootstrap (cases, then repeats inside each case, resampled; the whole scoring and the ladder unlocks re-run; fixed seed). # = rank; "2=" = indistinguishable from the model directly above (the paired 90 % interval of their Overall difference contains 0; a tie group chains adjacent ties only). Efficiency = the share of rows that finished inside the standard output budget (a row that ran out counts as failed on the board; Room = Overall with the end-of-run retry at the next rung swapped in, "=" when nothing ran out, "…" while a retry is pending). Deep RP, Conversation, Character, Creative writing rest on one or two standard cases (one repeat each): their ± is the bootstrap's floor, not a measured spread — read them as rough. Chat: one generated session per case (±~0.1 per case between runs); items every model on the 2026-10-31 board passed weigh half. NSFW RP: 3 sessions, ± is a 3-case bootstrap, read as rough.*

*Class = a coarse capability band from Overall (profile headline.classes): S ≥ 16, A ≥ 13, B ≥ 10, C ≥ 0 (no Overall yet = "-"). Lean = (Chat − board median Chat) − (Coding − board median Coding): chat-leaning or code-leaning at ≥ 1.5 points, else balanced.*

*Run cost = what the standard Code and Chat tests of one run cost at the provider's price (generation only; the judge runs locally; cached input is priced at the model's cache-read rate where the registry declares one, else at list; no off-peak discounts; retries and bonus sets not counted). ~ = estimated from token counts at list price. * = some requests returned no usage data. NSFW tests are priced separately. — = run on our own hardware.*

### Points

| Model | Coding | Chat | NSFW (separate) |
|---|---|---|---|
| deepseek-r1-0528-8b | 0.0 (partial: rows missing, not scaled) = Func pending + Predict pending + Tools 0.0±0.1 | - | - |
| gpt-6-1-terra | - | 2.3 (partial: rows missing, not scaled) = Deep RP 2.3±0.1 + Conv pending + Char pending + Writing pending | - |
| qwen2.5-vl-7b-caption | - | 0.4 (partial: rows missing, not scaled) = Deep RP pending + Conv pending + Char pending + Writing 0.4±0.1 | - |
| claude-haiku-4-5 | - | - | 2.0 (partial: rows missing, not scaled) = Unc 2.0±0.1 + RP pending + Writing pending |
| kimi-k3 | - | - | - |

*Sub-score maxima: Coding = Functions 5 + Predict + bug hunt 2.5 + Tool use 2.5; Chat = Deep RP 3 + Conversation 3 + Character 2 + Creative writing 2; NSFW = Uncensored 2 + NSFW RP 4 + NSFW writing 4. Points = the sub-score's share × its maximum; "pending" = no item yet (its category is scaled up from the measured points, stated in the cell). ± in points.*

### Ladders

| Model | chat/character sets L1 | chat/conversation sets L1 | chat/deep_rp sets L1 / L1 | chat/writing sets L1 | nsfw/nsfw_rp sets L1 | nsfw/nsfw_writing sets L1 / L1 | nsfw/uncensored sets L1 | work/agentic sets L1 | work/code sets L1 | work/reasoning sets L1 |
|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-r1-0528-8b | locked | locked | locked / locked | locked | locked | locked / locked | locked | locked | locked | locked |
| gpt-6-1-terra | locked | locked | locked / locked | locked | locked | locked / locked | locked | locked | locked | locked |
| qwen2.5-vl-7b-caption | locked | locked | locked / locked | locked | locked | locked / locked | locked | locked | locked | locked |
| claude-haiku-4-5 | locked | locked | locked / locked | locked | locked | locked / locked | locked | locked | locked | locked |
| kimi-k3 | locked | locked | locked / locked | locked | locked | locked / locked | locked | locked | locked | locked |

*A ladder pack is a set of harder questions worth 1 point together (partial credit inside); the sets of a level are given only after ≥ 90% on the standard level and every set of every lower level. "pending" = unlocked, not run or not judged yet (0 for now). (set n) after a score = the highest level scored.*

## Components

| Model | Programs | Code bank | Agent | Tools | Deep RP | Willing | NSFW (gated) |
|---|---|---|---|---|---|---|---|
| deepseek-r1-0528-8b | - | - | - | 0 | - | - | - |
| gpt-6-1-terra | - | - | - | - | 78 | - | - |
| qwen2.5-vl-7b-caption | - | - | - | - | 20 | - | - |
| claude-haiku-4-5 | - | - | - | - | - | 100 | - |

*RP = 40% identity (who plays whom) + 25% continuity/OOC + 35% craft; identity and continuity blend the judge with deterministic checks. NSFW = 45% erotic + 25% craft + 30% brief/limits kept. Story = ¾ judge + ¼ checks. Explicit peak = mean over the explicit-required briefs of ½ judge explicitness + ½ that row's check pass rate (a model with no such rows: the old max over the untagged rows). Willing = mean over tagged briefs — must-write: written, not sanitized, no lecture, explicitness elements met (no tagged rows: the share of untagged NSFW prompts written). Steer = the judge said obeyed AND every judge element and (re-run) check passed. Programs, Tools, Instruct, Reason = pass rate. Deep RP = mean per case of the weighted share of facts delivered in character (n/a when the context is under the case's 40k). Code bank / Agent = mean per task of the share of hidden test groups passed, halved when existing behaviour broke (and, for Agent, when it pushed/deployed or edited tests); see the Code bank table. A component not measured is left out and the others renormalised. Failed checks with evidence: `failures.md`.*
