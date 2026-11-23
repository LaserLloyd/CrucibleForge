# Judge marks 4.5: notes

`judge-marks-45.json` holds the marks; `scripts/check_judge_marks.py` tests them. These notes record what the
marks file cannot say in a field.

## Revision of 2026-11-19

The first marks (written before any reader run) tested single decisions: raw agreement and AC1 per fact, and per
pairwise ladder comparison for sessions. A tool check on stored rows (two models, one second reader, thinking
off) showed that this tests the wrong unit:

- **Long scenes.** The production judge and the reader agreed on 86% of fact decisions (AC1 0.75). The reader
  was the stricter one: judge yes / reader no on 16 disagreements, the reverse on 7. Four disagreements were
  facts zeroed by the judge's lone out-of-character flag on turns the code calls in character. D1 (b) now
  removes that cause (`rpdeep.ooc_flag_counts`).
- **Sessions.** The readers agreed on 61% of comparisons (AC1 0.43), but the disagreement was unbiased
  scatter: the reader gave the subject more on 55 and less on 53. 63% of the disagreements were near misses
  (one reader split). Comparisons at rung R1 agreed 94%, and those at R2–R4 agreed 57%, because the ladder
  search asks close comparisons by design. The placements the points come from agreed: distance-weighted
  0.92, part means within 0.01.

The marks now test the unit that carries points:

- **Primary.** The judge term a part adds to its 90% margin must be at most 5% of the part's maximum. The
  reader must also keep the order of every pair of models further apart than their combined margin.
- **Sessions.** Agreement is measured on placements.
- **Informational only.** The per-decision figures stay in the output.
- **Long scenes.** D1 (a), (b), (d), (e) and (f) stay hard checks.
- **Reader family.** The reader must be of another family than both the judge and the graded model. Units no
  configured reader fits are "agreement not measured", and the part stays pending.

### Frozen for 4.5.0 (lead, 2026-11-19)

- These are marks v2, as first committed on 2026-11-19. They are frozen for 4.5.0. `check_judge_marks.py` writes
  the file's hash into `judge_marks.json` (`marks_sha256`). The board compares it with this file at render, and a
  result checked against any other marks leaves every judged part pending.
- Marks v1 failed on the stored rows: sessions reached AC1 0.43 against a mark of 0.60.
- Marks v2 was written after those rows had been seen. It is therefore tested only on rows generated after it was
  written: the 4.5.0 launch runs.
- If v2 fails on the launch rows, the judged parts read "pending" in 4.5.0. The marks are not revised again for this
  release.
- The marks are checked per model. A model's judged part counts only when the agreement run read that model's own
  units with a fitting reader, and only for the runs it read (`covered` in `judge_marks.json`). Otherwise the part
  reads "pending: judge agreement not measured for this model".

## Draft marks for 4.5.1 (not used by 4.5.0)

`judge-marks-45.1-draft.json` has the same structure and the same limits as the frozen file. Only the long-scene
score changes: the mean of the judge and one second reader from a different family than both the judge and the
graded model. The 4.5.0 code never loads it (a test asserts that). sha256
`86c28ffb6379f9392d195fb6c2e07a22d8d89af2838b408f4843299868ff6aa8`.
This file was written 2026-11-19, before any reader run it will be tested on.

## For the owner: proposals that change how points are awarded (not implemented)

These were estimated on the same stored verdicts of two models. Each one changes the scoring contract, so each
needs the owner.

1. **Agreed decisions only.** A decision the two readers disagree on is left out of the part's score and
   counted, like a cut reply.
   - Long scenes keep 86% of decisions; DS flash 0.657 and the reference 0.697 (judge alone 0.651 / 0.711).
   - Sessions keep 61% of comparisons; the gap between the two models widens (0.787 vs 0.603).
   - Cost: none beyond the second reading.
   - Risk: a model that writes ambiguous replies loses decisions rather than points.
2. **Mean of the two readers.** The part's score is the mean of both readers' unit scores.
   - The judge term falls by about 1/√2.
   - Reader calls per model double, for every model, every release.
   - Estimated long-scene rates: DS flash 0.627, reference 0.681.
3. **Third reader on disagreed decisions only.** A third model-family reader breaks ties.
   - Extra calls: +14% for long scenes and +39% for sessions on these rows.
   - Agreement with the final decision cannot be estimated without a third reading.
