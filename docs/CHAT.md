# CrucibleForge — the Chat section (suite 3.4.0)

The Chat half of the benchmark: what each case measures, how it is scored,
where the criteria come from, and what it costs in run time. Installed
2026-09-23; the cases live in `cases/rp.json`, `cases/nsfw.json` and
`cases/story.json`, the rubrics in `crucibleforge/judge.py`, the
deterministic checks in `crucibleforge/session_checks.py`, the aggregation in
`crucibleforge/report.py`.

## 1. The case set

The Chat half is **13 judged rows** (was 14) from **27 generation calls** (three
multi-turn sessions). Every case packs several checks together, and each has a
**deterministic grader** alongside the 122B judge, so identity and continuity are measured with
regexes and not only by the judge's opinion.

| New case | Cat. | Turns | Replaces | Main thing it measures |
|---|---|---|---|---|
| RPS1-hollow-reach | rp | 6 | RPM1, RP1, RP4 | continuity with planted facts, an OOC **retcon** plus a "who knows the secret" probe, a POV switch from 3rd past to 1st present, recall under the retcon |
| RPS2-swap-seats | rp | 5 | RPM2, RP2 | **"who's me and who's it"**: the user controls two characters, then an OOC **role swap** (the model now plays the user's character and the user plays the model's), then a swap back, with an OOC "who did you play" probe |
| RPX1-card-table | rp | 1 | RP3 | 3-NPC ensemble with hard speech rules (no contractions, questions only). The user tries to cheat, so outcomes must be honest, not sycophantic |
| RPX2-user-writes-npc | rp | 1 | RP4 | the user writes the NPC's reaction and it breaks the card's core trait. The model must keep the character truthful without breaking the fiction |
| NX1-tuning-fork | nsfw | 1 | N2-explicit, N3-graphic | graphic scene under 10 stacked craft constraints (close 3rd present, open on dialogue, oblique backstory, in-voice check-in that changes things, a funny beat, object callback, banned stock phrasing, reframing last line) |
| NX2-juniper | nsfw | 1 | N4-kink | negotiated rope scene. Three sections with two different first-person POVs and tenses. Safeword and slow-word are used in the scene. **Two hard limits are checked deterministically** |
| NX3-thaw | nsfw | 1 | N1-suggestive | register control: sexual tension with **no** explicit content, carried by subtext. Tests the model that can't stop at "charged" |
| NMX1-needle-and-gull | nsfw | 6 | NM1-escalation | explicit ERP in character. Never narrates the user's arousal or reactions. OOC pacing ("slow down, don't write my reactions") must be applied. A fresh-tattoo injury and a stated limit (no neck marks) must persist through the sex. Recall at the end |
| ST1-ten-elements | story | 1 | new | a 900–1200-word story with 10 required elements that must change the plot (lechmazur method) and a letters-structure constraint |
| ST2-green-door | story | 1 | new | continue a given opening (original text, about 370 words) in the same voice, POV and tense, keeping every planted detail and paying off two setups |
| S1–S3 | steer | 1 | — | unchanged |

The replaced cases (RP1–4, RPM1–2, N1–N4, NM1) stay in the suite files, out of the benchmark.

## 2. Why: where the 3.3.0 Chat set saturated

Computed from the latest judged row per model of the 2026-09 board (31–41 models, now in
`results/archive-2026-09-23/`). The table below covers the ~15 contenders, excluding the 256M–7B tail:

- **RP agency**: 10/10 for almost every contender, mean 9.2–9.5 overall. It gives no signal. One
  reason is that the rubric had no caps. `split-untied-31b` RPM2 narrates the user ("Once you've
  settled in…", "slides your usual drink across") and still gets agency 10.
- **RP character**: 9–10 for contenders. **RPM consistency**: c9–c10 with `recalled_detail` 35/40
  and 32/40. The recall probes are one-hop and never contradicted by a retcon.
- **N1–N4 erotic**: contenders sit at 7–9 with prose 7–9. Most of the spread comes from refusals
  and small models, not craft. `willingness` is 100% for nearly every uncensored model.
- **N3-graphic** is the only case whose quality separates models. Forcing "graphic" exposes crude
  prose (prose 4.4±2.8). The new NSFW cases keep that pressure and add constraints.

## 3. Design principles (and where each came from)

1. **Stack constraints so the checklist discriminates** [lechmazur, EQ-Bench adherence].
   Required elements must *change the story*. A mention scores half.
2. **Plant facts, contradict them with a retcon, then probe** [Fiction.liveBench theory-of-mind /
   chronology; RP-Bench Continuity + Temporal Reasoning]. This is harder than plain recall. The
   model has to use the *new* fact and drop the old one.
3. **Make identity failures cheap to detect and expensive to commit.** the maintainer's complaint is that
   models "forget who's me and who's it". RP-Bench measures per-turn agency and POV/tense violation
   rates and puts Agency Respect in its heaviest tier. The 2q SillyTavern notes score "perspective
   discipline" as a hard format check. So identity gets (a) regex checks, (b) judge caps, and (c)
   the largest weight in the RP score (40%).
4. **Test OOC as a skill** [RP-Bench instruction adherence, community `((OOC))` convention]. The
   reply must be brief, correctly answered, back in character, and the change must be applied
   later. That last part is checked deterministically (e.g. `brass` must never reappear after the
   retcon to `iron`).
5. **Flaw-hunter judging with anchored bands** [RP-Bench "Flaw Hunter", EQ-Bench "be a critic"].
   The judge lists quoted flaws, starts at 10 and deducts. 5 = typical AI fiction, 9 = rare. This
   pulls contenders off the 9–10 ceiling.
6. **Penalise slop explicitly** [EQ-Bench slop lists and anti-verbosity/anti-metaphor pairwise
   criteria; RP-Bench cliché detectors; Jericho Writers' cliché/purple/euphemism list].
7. **Consent as craft, not as a disclaimer** [RP-Bench S.9 consent-agency; Writing Through the
   Body; writingworkshops "signals before, during, after"]. Negotiation, safeword use and hard
   limits are *requirements that must hold*, and the hard limits are regex-checked.
8. **Shrink the test, not the model** (maintainer). There are fewer conversations, not tighter caps.
   RP/NSFW turns get 2000 tokens and stories 3000. Thinking models get ×4 (8000 a turn, 12000 a story — under the profile's
   24576 cap, which never binds on chat).

## 4. Per-case rationale and expected discrimination

**RPS1-hollow-reach** (6 turns, `rp_session`). The card has 2 NPCs with distinct voices and a
3rd-person-past rule. T1 plants a brass key, five bells and the satchel. T2 has a secret told
privately to Tamsin (desertion). T3 is an OOC retcon (the key is iron; the bandage is on Odile's
right hand, not Tamsin's) plus a structured `KNOWS: | UNAWARE:` probe. T4 is a time and location
jump with a new NPC. T5 is an OOC switch to **1st person present as Tamsin**. T6 is an
in-character recall of the three retconned facts.
Failure modes it catches:
- saying "brass" after the retcon
- keeping Tamsin bandaged, including "my bandaged hand" once in Tamsin's head
- Odile "knowing" the secret
- first-person narration drifting back to "Tamsin…"
- narrating Corin

Expect contenders to split roughly 60–95% on checks, which RPM1/2 never produced.

**RPS2-swap-seats** (5 turns, `rp_session`). Second-person-present card, which is the convention
where "you" confusion lives. The user plays Jun (they/them) *and* Pell. T4 swaps seats: the model
plays Jun and "you" now means Mara. T5 swaps back and asks `LAST TURN I PLAYED: | PELL WANTS:`.
This is the most direct test of "who's me and who's it". It also tests they/them pronouns (judge)
and the user-owned second character (regex).

**RPX1-card-table** (1 turn, `rp_scene`). Ensemble voices with hard, checkable speech rules. A
risky user action must get an honest outcome [RP-Bench anti-sycophancy].

**RPX2-user-writes-npc** (1 turn, `rp_scene`). The user godmods the NPC against the card's core
trait. It tests character integrity versus compliance and staying in the fiction (regex: no OOC).
In today's data every model complies with user framing. This case rewards the ones that keep the
character truthful.

**NX1-tuning-fork** (1 turn, `nsfw_craft`). This replaces "be graphic" with a 10-item brief.
The deterministic checks are:
- opens on dialogue
- the fork appears 2 or more times
- present tense
- close third (no narrator "I")
- the banned-phrase list
- length

On existing N2/N3/N4 outputs, 35–58% of models avoided the banned phrases *without being told
to*. Told explicitly, it becomes a real instruction-following discriminator for erotic prose.

**NX2-juniper** (1 turn, `nsfw_craft`). The deterministic checks are:
- 3 exact headings
- per-section tense (past / present / past)
- 1st person in "During"
- `juniper` + `amber`, with amber used in "During"
- the mouth hard limit is never breached in During/After (negation-guarded, so "nothing over my
  mouth" in the negotiation is fine)

Two distinct inner voices are judged.

**NX3-thaw** (1 turn, `nsfw_craft`). Register control. `register_miss` is flagged if it goes
sexual, kisses, or touches early. Regex checks: no kiss in narration, no explicit lexicon. It
does not hurt `explicitness_peak` (that is a max). It rewards subtext [RP-Bench 2.6/3.1].

**NMX1-needle-and-gull** (6 turns, `erp_session`). This is where ERP models actually fail. The
checks cover:
- narrating the user's orgasm and reactions ("he groans", "his breath catches"). In a dry run over
  the old NM1 transcripts, 13/31 models narrate the partner's reactions. That was allowed in NM1
  but would be a violation here.
- ignoring "slow down"
- neck marks after "nothing on his neck" (10–15% of models bite or mark necks unprompted in NM1)
- pressing on a fresh tattoo
- recalling the aftercare and the limit after four turns of sex

It also feeds `explicitness_peak` and `willingness` (sanitized / refused as before).

**ST1-ten-elements** (`story`). The lechmazur 10-element brief plus a letters structure (3×
"Dear Anselm," — regex). The ending constraint targets EQ-Bench's "incongruent ending positivity"
and "unearned transformation".

**ST2-green-door** (`story`). A continuation of an original opening. It tests voice matching and
prose-level continuity. Regex checks: the dog stays three-legged, no supernatural reveal,
first-person, past tense, length. The judge gets the full canon in `judge_key`.

## 5. Deterministic grader (`session_checks.py`)

Case field `"checks": [...]`. Types: `no_puppeting`, `forbid_regex`, `require_regex`,
`require_all`, `ooc_reply`, `ooc_field`, `tense`, `word_range`. Scopes: `narration` (quotes and
OOC removed), `dialogue`, `ooc`, `ic`, `all`, plus optional `section` (a markdown heading) and
`negation_guard`. Groups: `identity`, `continuity`, `ooc`, `constraint`. The row gets
`checks.{results, groups, rate}`. Every failure carries the offending snippet, so a false
positive is visible in failures.md.

**Validated against real data** (the existing transcripts, not only fixtures):
- The name-based puppeting check on the RPM1 user "Ren": **0/31 hits**. On NM1's "Mateo" (where
  narrating him was legitimate) it fired on 13/31, every hit a genuine narration of that
  character's reactions. So precision is good.
- The second-person check started at 19/31 false positives on RPM2. Most models leave dialogue
  unquoted and say "you said…". Tightened to present tense, main-clause "you" only, and it
  now has **0 hits on 147 RP rows** (RPM2 + RP1–4). Positive fixtures ("You nod…", "…, and you
  step inside") still trip it.
- The tense classifier was decisive on 58/60 long scenes (N2/N4). It abstains on short,
  dialogue-heavy turns (`min_evidence`) rather than guessing.
- `tests/test_chat_section.py`: a well-behaved RPS1 and RPS2 transcript passes **every** check.
  Each planted failure (puppeting, wrong KNOWS list, brass after retcon, 3rd-person slip in
  1st-person mode, wrong recall, playing Mara after the swap, wrong OOC answer) trips exactly its
  intended check(s). `crucibleforge cases verify` checks every case's `checks` for known
  types/groups/scopes, required keys, compilable regexes and in-range turn indexes.

To make quote-stripping reliable, every card requires dialogue in double quotes. The
`dialogue-quoted` check catches models that ignore this, and that is itself an instruction
failure.

## 6. Judge rubrics (`judge.RUBRICS`)

The new rubrics are `rp_session`, `rp_scene`, `nsfw_craft`, `erp_session` and `story`. All are
0–10 integer dims and boolean flags, so `parse_verdict`, salvage and aggregation work unchanged.
The legacy `rp_single`/`rp_multi`/`nsfw` stay for old rows.

Criterion → source (full URLs under Sources below):

| Criterion | Source |
|---|---|
| identity (+ hard caps), POV/person rules | RP-Bench 1.1 Agency Respect + POV/tense drift; 2q "perspective discipline"; the maintainer's "who's me and who's it" |
| continuity (retcons, who-knows-what, injuries) | RP-Bench 1.3 Continuity, 3.12 Context Integration, 3.13 Temporal Reasoning, S.8 anatomical coherence; Fiction.liveBench theory of mind/chronology |
| ooc | RP-Bench instruction adherence / instruction drift; SillyTavern `((OOC))` convention |
| voice | RP-Bench 1.5 Distinct Voices; PingPong character consistency; EQ-Bench consistent voice |
| craft / restraint / prose | EQ-Bench (elegant prose, purple prose, overwrought, tell-don't-show, avoids flowery verbosity & gratuitous metaphor); RP-Bench 2.1/2.2/2.5; 2q reporting-style avoidance |
| initiative, integrity | RP-Bench 2.3 anti-sycophancy, 2.4 anti-perfection; PingPong entertainment; EQ-Bench believable character actions |
| calibration | RP-Bench 1.4 Length Calibration, 2.7 Pacing |
| erotic | RP-Bench 3.1 Earned Intimacy + 3.11 Erotic Craft, S.7 escalation pacing; Writing Through the Body (tension, balance); writingworkshops (ebb and flow) |
| emotion, character (NSFW) | Jericho Writers (emotion over mechanics, sex reveals character, consequence) |
| constraints / checklist | lechmazur (integration over inclusion); EQ-Bench adherence |
| consent / limits | RP-Bench S.9 consent-agency; Writing Through the Body; writingworkshops signals before/during/after |
| ending | EQ-Bench incongruent ending positivity, unearned transformations |
| explicitness scale | unchanged (keeps `explicitness_peak` comparable) |
| flaw-hunter scoring + calibration band | RP-Bench Flaw Hunter; EQ-Bench "you are a critic" |

`build_judge_input` additions:
1. The case's `judge_key` (canon and expected answers) is shown as an ANSWER KEY, outside the
   model-output fence so it can't be forged.
2. The judge runs at a 16384-token context with 8192 tokens for its thinking + verdict, so a
   judge prompt must stay under ~8K tokens. Only when a prompt would not fit are the model's
   turns clamped **for the judge** (head + tail, with a marker the judge is told is ours):
   earlier turns share the room equally, the final turn gets 2.5 shares. The budget follows the
   judge's configured context and `max_tokens`. At a conservative 3.2 chars/token (the rig's
   Qwen transcripts measure 3.2–3.7) the worst case — every reply at its full token budget — is
   ~7.96K tokens for RPS1/RPS2/NMX1 and ≤ 6.3K for every single-turn case; typical sessions
   (400-word turns) are shown whole at ~5.6–6.7K. The regexes always read the full text.
3. The judge scores the longest inputs (the sessions) first, so they are not the last wave.

Canary: six probes, now including `godmod` — a blatantly user-puppeting session must score
identity ≤ 4 or the batch aborts — and the `explicit` probe scores on `nsfw_craft`. The probes
run concurrently on the judge's slots.

## 7. Scoring: how it feeds Chat

Weights (`profiles/bench.yaml` `scoring: chat:`): **RP 20 · NSFW 15 · Story 10 · Explicit peak
5 · Willing 5 · Steer 5**. A component that was not measured is dropped and the rest renormalised.

- **RP (0–10)** = 0.40·Identity + 0.25·Continuity + 0.35·Craft
  - Identity = ½ judge `identity` (rp_session, rp_scene, erp_session) + ½ deterministic identity
    pass rate. This pools RP *and* ERP rows, because puppeting is the same failure in both.
  - Continuity = ½ judge (continuity, ooc) + ½ deterministic continuity+ooc rate.
  - Craft = judge voice/craft/initiative (+ integrity/calibration on scenes).
- **NSFW** = 0.45·erotic + 0.25·mean(prose, emotion, character/voice) + 0.30·constraints
  - constraints = ½ judge `constraints` + ½ deterministic constraint/continuity rate.
  - Stored as `nsfw.erotic_quality` (the NSFW component); the raw erotic mean is `erotic_raw`.
- **explicit_peak, willing**: unchanged formulas over rubric ∈ {nsfw, nsfw_craft, erp_session}.
- **Story** (new) = ¾ judge mean of 7 dims + ¼ deterministic rate.

Failed checks are listed per case, with the offending snippet, in `failures.md` ("Chat checks"),
next to the judge's `register_miss` / `limit_violated` flags. Rows from 3.3.0 and 3.4.0 never
share a board (the case-set hash differs), so every model needs a chat re-run; the Coding half
is untouched.

## 8. Run time

Nothing was shrunk on the model side: 2000 tokens a chat turn, 3000 a story, thinking models ×4
(8000 / 12000 — the profile's 24576 cap never binds on chat). The time box comes from fewer,
denser rows.

**Generation.** The chat half's long pole is the 6-turn session: 6 *dependent* generations. The
runner schedules the three sessions first, next to the long coding rows. Dry projection of the
whole bench on an 8-slot model (a pool simulation over the real case list; per-row token counts
= completion tokens, reasoning included, measured on the archived 2026-09 runs of nine 27–35B
thinking models; the new chat turns drawn from the old RP/NSFW turn distribution: median 991,
p90 3781 tokens):

| per-slot speed | every row at its median | every row at its p90 (pessimistic) |
|---|---|---|
| 34 tok/s | 12.1 min (chat done at 7.5) | 19.3 min |
| 30 tok/s | 13.7 min (chat done at 8.5) | 21.9 min |
| 20 tok/s | 20.5 min | 32.7 min |

Scheduling the sessions first matters at the tail: with the old longest-category-first order the
p90 projection is 23.1 / 26.2 / 39.1 min. One 6-turn session alone is 3.5 min at the median and
12.8 min at the p90 (30 tok/s); a model that burns its full 8000-token budget on **every** turn
would take ~27 min for that one session — that is a model overflowing on every turn, and the
overflow shows in failures.md.

**Judge** (122B, 4 slots). Measured 2026-09-22 on the old set: ~1 min lease + load, ~75 s a
verdict, 14 rows in ~6 min. Measured 2026-09-24: the 6-probe canary takes 266–421 s (its slowest
STRICT probe thinks 3.5–8k tokens at ~17–20 tok/s a slot); 13 rows = 4 waves, the
sessions first with 6–8K-token inputs and a flaw-listing rubric that thinks longer
(~120–180 s a wave) → **~12–16 min** total. The per-verdict ceiling (`judge.row_timeout_s`,
600 s) bounds the worst wave.

If a thinking model still overruns, the knob is the case set, not the budget: RPS1 can drop to 5
turns by merging T4 into T5 (checks re-index T5/T6 → T4/T5).

## 9. Content limits

Every sexual case involves only named adults with stated ages (29–44), consenting, with
negotiation where kink is involved. There are no minors or age-ambiguous characters, no
non-consent, no incest (Pell is Jun's uncle in a non-sexual salvage scene only), no real people,
and no bestiality. The FC safety cases are untouched and stay out of the benchmark.
`tests/test_chat_section.py` re-checks every bench NSFW case for stated adult ages (all ≥ 21 in
the text; 29–44 in fact), stated consent, and none of the excluded themes.


## Sources

- EQ-Bench Creative Writing v3 — about/methodology: https://eqbench.com/about.html
- EQ-Bench creative-writing-bench repo (criteria, negative criteria, judging prompt, pairwise
  prompt, slop lists): https://github.com/EQ-bench/creative-writing-bench —
  `data/creative_writing_criteria.txt`, `data/negative_criteria.txt`,
  `data/creative_writing_judging_prompt.txt`, `data/pairwise_prompt.txt`,
  `data/slop_list_trigrams.json`
- lechmazur writing benchmark (10 required elements, integration over inclusion):
  https://github.com/lechmazur/writing
- RP-Bench (LeviTheWeasel) — 27 dimensions, NSFW track S.7–S.9, Flaw Hunter, slop detectors:
  https://github.com/LeviTheWeasel/rp-benchmark and rubric
  https://raw.githubusercontent.com/LeviTheWeasel/rp-benchmark/main/analysis/scoring_rubric_v2.md
- PingPong (user emulation, character consistency / entertainment / fluency):
  https://arxiv.org/abs/2409.06820
- RPBench-Auto (Boson AI; character- and scene-based multi-turn):
  https://www.boson.ai/blog/rpbench-blog
- Ayumi's LLM RP & ERP ranking (ERP lexical score/variety — the idea behind the "explicit when
  asked" lexicon check): https://rentry.co/ayumi_erp_rating_archive2
- Fiction.liveBench (theory of mind, chronology, tracking characters):
  https://epoch.ai/benchmarks/fictionlivebench
- SillyTavern novel-style RP model notes (perspective discipline, reporting-style avoidance):
  https://note.com/aa4666lo/n/n916d11e8f4f9?hl=en
- Jericho Writers — Complete guide to writing sex in fiction:
  https://jerichowriters.com/complete-guide-to-writing-sex-in-fiction/
- Writing Through the Body — Rules for writing erotica:
  https://writingthroughthebody.com/rules-for-writing-erotica-how-to-craft-passionate-and-compelling-stories/
- Writing Workshops — How to write sex scenes (craft techniques):
  https://writingworkshops.com/blogs/news/how-to-write-sex-scenes-craft-techniques
- RMTBench (user-centric multi-turn RP), for context: https://arxiv.org/html/2507.20352v2

Only methods and criteria were borrowed. No benchmark prompts or copyrighted text were copied.
All characters, the story opening and every prompt are original.

