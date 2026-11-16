# Developing a pack (4.5)

For an outside contributor who wants to add a case to a hard set, write a new hard set, or rotate one.
Every scored case, key, reference, mutant and tool is public and runnable; there is nothing you cannot
read or re-run. Read `docs/CONTENT-RULES.md` before you write anything.

Everything in this guide exists in the tree. A few steps (the fixture run, the judge-time element checks) need the judge and so are run at pack acceptance, not by `cases verify`.

## 1. What a pack is

A **pack** is the unit of **authoring and rotation**: the 5-6 hard cases of one score (Code, Chat, NSFW)
that are written, tested, certified and later replaced together. It is **not a scoring construct**.
Every model runs every case in the same pass; there is no unlock, no screen and no "not run" state.
Each score is /10: the standard cases give 8, the hard part gives 2. The pack id sits in `meta.pack`.

It is not the same thing as the 4.1 "optional pack" (a ladder `set-<n>.json` that a model unlocks, see
`docs/PACKS.md`). Those are the old mechanism; hard cases are not unlocked.

## 2. Files

    cases/hard/<score>/<rev>.json     the scored set for one revision (score = code | chat | nsfw)
    cases/hard/<score>/shelf/         candidate bank: uncertified, never loaded, never scored
    profiles/bench45.yaml             scores.<work|chat|nsfw>.hard: {max: 2, file: cases/hard/<score>/4.5.json}

Revision file: `{"schema": 1, "score": "code", "rev": "4.5", "cases": [ ... ]}`. A case is a standard case
of that score's kinds plus a `meta` block and a `category`. The full format, the allowed kinds and the
promotion rule are in [`cases/hard/SCHEMA.md`](../cases/hard/SCHEMA.md); do not duplicate it here.
Required `meta`: `authorship`, `budget.anchor_tokens`, `pack`, `certified` (true in a scored file, false
only in `shelf/`). Ids must be unique across the standard cases and every hard file. No scored case may
live under `cases/private/` or a `*.local.*` path.

## 3. Writing rules

- **The spec states every rule that is tested.** If a check, test group or judge element rewards
  something, the prompt said so. A hidden requirement is a defect in the case, not a hard case.
- **Stated output scale is question design, never scored.** A prompt may say "two short paragraphs" or
  "only the function" to size the answer. No element, check or test rewards brevity or length; a verbose
  model costs time, not points. **No output caps and no thinking caps**: `max_tokens` stays at the
  per-case budget.
- **Prefer checkable.** Use `exact`, `numeric`, `python_exec`, `tool_call` where the skill can be checked by a
  program. Use a judged rubric only where it cannot.
- **Judged cases follow the element standard** (ELEMENTS-45): one requirement per element; an explicit
  "NO if" clause; the quote instruction (the judge must answer YES with a verbatim quote of 12+
  characters); a `gold_quote` (author-side, never shown to the judge); `turns` set; an evidence type
  (`single`, `multi`, `negative`); a `class` (`fact` or `craft`, which sets the sample count; never an
  element-level `samples`). Everything countable (headings, word counts, banned phrases, regexes) goes in
  deterministic session `checks`, not in the judge. See `docs/JUDGE.md` for classes and sampling.
- **Length.** Chat and NSFW hard sessions are short (at most 3 turns in the 4.5 design; the schema allows
  the same kinds as the standard parts). Keep the decisive events (memory callbacks, state changes) and drop
  connective turns.
- **Do not rephrase an existing case.** Test a skill, or a nastier version of one, that the standard parts
  do not already measure.

## 4. Content rules and the NSFW path

`docs/CONTENT-RULES.md` binds every case, public or private, whoever or whatever writes it: no sexual
content involving minors; no sex with non-sapient animals; no weapon uplift; no working real-world harm;
no living real people. Everything else in adult fiction is in scope. Adults are stated in the text of the case.

**Claude, or any assistant acting as one, never writes explicit prose.** Grok, DeepSeek, MiniMax or a
local tune writes it. The assistant writes specs, structure, keys, element text, checks and code.

NSFW hard cases are public. Before any NSFW hard commit:

1. **Content attestation** by two independent models (Grok and Kimi) against the CONTENT-RULES checklist.
   Both must pass every case. When Grok wrote the case, DeepSeek attests as a third rater (a writer never
   attests alone).
2. **Privacy scan** (automated): nothing from private material, no names or infrastructure details.
3. **Structure and key review** by a reviewer that does not write the prose.
4. **Owner look**: the repo owner reads the cases from a review link. The dev commit waits for it.
5. Dev commit, then the release-time security/ship review.

Keep explicit text in the case file only; do not paste it into docs, issues or logs. NSFW hard cases are
written by Grok and DeepSeek only, and a model whose family wrote a case is shown "not comparable" on NSFW
hard rather than scored on a different form.

## 5. Authorship

- Set `meta.authorship` to the family that wrote the case prose (`grok`, `deepseek`, `minimax`, `local`,
  `human`; `cases verify` requires a non-empty string). Record key writer and key reviewer in the
  authorship ledger as well.
- **At most 40% of a set's cases from any one family.** A fallback that changes the family re-runs this
  check before the case is committed. (Checked by review today; no tool enforces it.)
- **A different family reviews the key** of every case: the reviewer is neither the case writer nor the
  key writer.
- **A family never labels the references of a case it wrote** (GOOD/BAD/planted-break YES/NO). Two other
  families label.
- The held-out subject models never write, filter, critique or label.
- The delta check after the pilot: an author family's score on its own cases must sit within the
  confidence interval of the cohort-relative expectation. Standard cases never use per-model exclusion.

## 6. Validation per case type

Run `uv run crucibleforge cases verify --profile bench45`. It checks the schema, unique ids, the meta, the
private-path rule, that max plus the standard parts add up to 10, and runs each case through the same
checks as a standard case. Then, per type:

| Type | Required proof |
|---|---|
| Code (`python_exec`, repo tasks) | `reference` passes the tests **100%**; **at least 5 `mutants`** (plausible wrong solutions) and **at least 90% are caught**; `mutants_expect` pins which test groups a mutant may still pass. `cases verify` runs these in the sandbox. |
| `exact` / `numeric` | a `verify.python` that re-derives the answer, so the key is not just a model's say-so. |
| Judged (chat, NSFW) | fixtures: a GOOD reference (`gold`) that gets YES on every non-negative element, a BAD reference (`foil`) that gets NO on the elements it is tagged to fail, and, for NSFW heat elements, a planted-break variant that gets NO. A key written by a model is the usual source of a bad question: check it by script or by hand. |
| Judged, element lint | EL-1..EL-16 (single requirement, "NO if", quote instruction, gold found in the reference, turns inside the case, no placeholders, class-only sampling).
  `cases verify` runs it **by default** for the hard files and for any profile with a `budget:` block (plain
  `cases verify --profile bench45`); `--lint-elements` forces it on any other profile. A case declaring
  `"element_standard": 45` is held to the standard (findings fail the verify); a 4.1 case gets the same
  findings as warnings. **Every hard case and every scored bench45 case must declare `element_standard: 45`**
  (a 4.1 case still waiting for its 4.5 rewrite, i.e. whose budget target is `pending_from_hash`, is exempt
  until its content changes). A `template: true` element in a scored case is always an error. EL-12 checks
  `replaces`/`carved_from` targets and that no previous-revision element id was dropped without a retire
  record (with a `note`) or a `replaces`; EL-13 checks `evidence` (`src: turn`) texts against the user turns;
  EL-15's structural half needs `fixtures/gold.md` (and `foil.md` when an element names `foil_fails`); EL-16
  also rejects a case in both `samples3_rows` and `samples1_rows`. |
| Judge fit | the card, conversation and a full-length answer must fit the 16k judge context. |

## 7. Run the budget check on every new or changed case

The 4.5 main test must fit a time budget (the anchor finishes in at most 49.5 minutes; the board average
at most 33). The hard sets are reported apart, but each case still has a design target.

    uv run crucibleforge budget --case MY-NEW-CASE      # see docs/BUDGET.md
    uv run crucibleforge budget                          # the whole main test and the hard sets

- Give every hard case `meta.budget.anchor_tokens`: the output the anchor should need (a thinker writes
  roughly 3-6k for a short function and ~2.5k per chat turn). Optional `meta.budget.judge_calls` and
  `meta.budget.prefill_s`.
- **A case more than +25% over its target** (measured or proxy) fails the check. If the cost is deliberate,
  put `"retarget_note": "why"` in `meta.budget` and raise the target in the same commit.
- A rewritten case has a new `case_hash`, so old rows stop counting and the number is an estimate until
  the pilot has run it.
- `uv run crucibleforge budget --require-measured` (or `budget.freeze: true` in the profile) is the freeze
  gate: it fails on any standard case whose tokens are only a design ESTIMATE, and a case whose content moved
  since its target (`pending_from_hash`) takes its judge calls from its elements, failing if they exceed the
  committed number by more than 25%. A board average that cannot be computed (anchor missing from the digest)
  is a failure, never a pass.
- Never lower a cap to fit. Cut turns, shorten the prompt or split the case.
- Limits for context: the hard sets add to the full-run time but do not count against the main-test limits.
  The anchor core rule (section 11) and the budget are separate constraints; satisfy both.

## 8. Filter cohort, rounds and confirmation

1. **Filter cohort**: a small panel (a cheap hosted model, one strong model that did not write the case,
   and several local models). Held-outs never take part.
2. **At most 3 development rounds.** Each round reads item statistics (difficulty, discrimination), the
   judge's disagreements and the planted-break results; fix or drop cases between rounds.
3. **One frozen confirmation run** on a balanced panel plus the held-out models. The cases are frozen
   first; selection is made on round statistics and certification on the confirmation only.
4. **Held-outs** (item behaviour generalises): difficulty, discrimination and reliability on the held-outs
   fall within the intervals of the development estimates, and the measured optimism is reported. It is
   not required that a held-out keeps its old rank; a valid harder test may reorder models.
5. A used confirmation set cannot be reused after a change: the next confirmation needs at least one new
   held-out model.

## 9. Bars: gates and diagnostics

| Kind | What |
|---|---|
| **Gate** (hard part) | at least 40% of items live in the top cohorts; the power target is met for the top cluster at the minimum detectable difference; validity (reference/mutant or GOOD/BAD) passes for every case. |
| **Gate** (judge) | the judge bars V1-V10 on the frozen elements (model-level error, leniency, sensitivity, gate-error rate and the rest; docs/JUDGE.md and the release notes). |
| **Gate** (NSFW) | P1: spread of at least 20, at most one model at 90 or above, best at 50 or above. P2: best between 20 and 50, and at least 25 below its own P1 for models with P1 of 50 or more. Plus held-out item behaviour. |
| Diagnostic | score spread and ceiling counts. They are reported, never gates. |

If a bar misses after 3 rounds, the pack waits; there is no relaxed fallback revision.

## 10. Certification and the shelf

A case is **certified** when it has passed the sections 6-9 checks and sits in the scored file with
`meta.certified: true`. Candidates not yet certified go to `cases/hard/<score>/shelf/`: they are never
loaded, never scored and fail `cases verify` if they appear in a scored file with `certified: false`.
Keep the build recipe (writer prompt, the brief, the key script) next to a shelf candidate so it can be
certified later without redoing the writing. A shelf pack is certified only when it is needed, on a fresh
confirmation run.

## 11. Rotation and promotion

- **Trigger**: at least 2 models have a bootstrap probability of 0.8 or more that their hard share is 0.9
  or more, in two consecutive refreshes.
- At the next revision: (1) hard cases that are now aced in both top cohorts are **retired**; (2) hard
  cases that still discriminate may **move into the standard parts**, **keeping their `case_hash`** so
  stored rows stay current (drop `meta` and `category`, add the case to `cases/<category>.json`); (3) the
  next certified shelf pack becomes the hard part.
- **Anchor core**: at least 50% of each score's cases stay unchanged in a revision, which limits how
  much one revision can change.
- Do not edit a certified case in place to make it harder: that changes its hash and discards its rows. Add a
  new case instead.

## 12. Worked example: the 4.5 Code hard part

*To be filled from WP-16 with measured effort.* It will show one Code hard case end to end (spec, reference,
mutants, `crucibleforge budget` row, filter rounds, confirmation) with the actual hours and API cost, and
replace the design estimate of two days of API and four hours of rig.

## 13. Checklist

- [ ] The spec states every tested rule; the output scale is wording only; no caps; nothing rewards brevity.
- [ ] `cases/hard/SCHEMA.md` format; `meta.authorship`, `budget.anchor_tokens`, `pack`, `certified`.
- [ ] Content rules read; adults stated in-text; no explicit prose written by an assistant.
- [ ] NSFW: two-model attestation, privacy scan, structure review, owner look before commit.
- [ ] No family above 40% of the set; the key reviewed by a different family; no family labels its own cases.
- [ ] Code: reference 100%, at least 5 mutants, at least 90% caught. Exact: key re-derived by script.
- [ ] Judged: GOOD/BAD/planted-break references behave; elements pass the element standard and lint.
- [ ] `uv run crucibleforge cases verify --profile bench45` passes.
- [ ] `crucibleforge budget --case <id>` run; within +25% of the target or a `retarget_note`.
- [ ] At most 3 development rounds, then one frozen confirmation with held-outs.
- [ ] Gates met; diagnostics reported; `certified: true` only in the scored file.
- [ ] Candidates left over are on the shelf with their recipe.
- [ ] If this replaces a pack: anchor core of at least 50% holds and promoted cases keep their hash.
