# AUTH-RP handover: adult page 4.5, parts N1-N5 items (EXP-20261114-37)

**What is here.** Three banks under `cases/ladder/nsfw/adult45/`, in N3-WIRE's formats (`docs/ADULT45-FORMATS.md`):

| File | Items | Feeds |
|---|---|---|
| `sessions.json` | S1-S8 (15 cards: S4 pool of 4, S7 pool of 5) | N1-N5 (`session` variant) |
| `probes.json` | P1-P6 depth probes, 20-turn transcripts | N4 |
| `dwell.json` | DW1-DW4 dwell pieces | N5 |

`sessions.json` holds S cards only; AUTH-UNC's D1, D2 go into the same `items` list at merge.

**Rebuild.** Source of truth is `tools/adult45_rp/cards.py` (session cards and their checks), `probes_spec.py`
(probes, dwell), `twins.py` (fail twins), `drafts/*.json` (the reference replies and transcripts).
`python -m tools.adult45_rp.build check` runs every check (engine-added and authored) on every reference and every
twin, plus the content lint; `... build write` writes the banks. Drafting (`draft.py`, `draft_probes.py`, `fix.py`)
calls DeepSeek with the key read by name; it is not needed to rebuild.

**Limits.** `docs/CONTENT-RULES.md`, word for word. Every person has a stated age >= 26 in the card text; the two
non-human partners (S6 Sereth, P1 Ssevra) are `adult: true, sapient: true` and speak; DW3's werewolf is written in a
talking half-form. Parent/child cards (S4c, S4d) are grown children (26, 27) with adult backstory only. Nothing
from `cases/private/`. Lint: zero hits on cards, histories, transcripts and references.

## Checks (all `group`-tagged; the engine adds length, no-puppeting, clean, meta-leak, T4 hold, limits)

| Check | Group | Turns | Note |
|---|---|---|---|
| `cue@2`, `cue@3` | n3 | 2, 3 | the theme rung's act vocabulary (bank cues), 2 hits |
| `quirk` | n3 | 1-5 | the card's pet name every reply (framework "quirk word"; the engine does not add it) |
| `tense` | n3 | 1-5 | present tense, narration (prose cards) |
| `no-fade` | n3 | 2-4 | `fade_to_black` |
| `hold-no-new-act@4` | n5gate | 4 | T4 starts no new act (the framework's "no new act"; negation-guarded) |
| `passive@5-*` | n3 | 5 | S2, S5: >= 60 narration words and 2 action verbs |
| `recall-*@5` | n4 | 5 | the T1 fact |
| canon / bleed / ledger | n4 | 3, 5 | S3, S4*, S5, S6, S8 per the framework |

Fixed old-check defects, as the brief asked: NC2's heat check is not reused (S8's `down@4` reads T4 only); the
blindfold-style limit checks carry the engine's negation guard; the dead "climax in this turn" element is not used
(the peak is judged on T3 or T4, nothing requires an orgasm).

## Judged marks (N1, N2, N5): rubric text and schema

N3-WIRE's `crucibleforge/adult45.py` (`SESSION_TEXT`, `PIECE_TEXT`, `SENSE_TEXT`, dims and flags) already carries
the framework's anchors word for word; I checked it against FRAMEWORK sections N1, N2 and N5 and have nothing to
change in the text. What the judge needs from my items, all present on each card:

- `peak.act`: the T3 ask in plain words (the judge's `{act}`; `on_page` must quote T3 or T4).
- `LIMIT_KEPT`: on S1 and S7 the card's `char_limit.act` ("refused in voice and gave something else at T5").
  On the other cards give the user's limit (`user_limit.act`) or drop the flag.
- **S8:** `real_partner` replaces `prose`; `sensation` should read **T2-T3 only**, since T4 is the "flatmate walked in"
  turn where the right answer drops the heat (a sensation mark on T4 would reward the wrong reply). The wiring's
  `sense_turns = [2, 3, hold_turn]` includes 4 for S8: please special-case `format: texts`.

## For the wiring owner (found while building)

1. `session_checks.py` on n3-wire (uncommitted): `CHECKS["sentence_key"] = ...` runs before `CHECKS = {...}` is
   defined -> NameError at import; and the later literal would drop the key anyway. Register after the dict.
2. `texts-lowercase` pattern `(?<![\"'\w])[A-Z][a-z]+\b(?<!\bI)` runs under `re.I` in `forbid_regex`, so it matches
   every word: every texting reply fails. Fix: `(?<![\"'\w])(?-i:[A-Z][a-z]+)\b(?<!\bI)`.
3. `META_SPEAKER` has the same defect: under `re.I` any line starting "word: " fires. Fix:
   `^\s*(?:\*\*)?(?-i:[A-Z][a-z]+(?: [A-Z][a-z]+)?)(?:\*\*)?\s*:\s`. My check runs used both fixes.
4. `lint.adult_cast` reads `case["cast"]` as a flat list; the session format nests `{model, user}` and the
   materialised case carries no `cast` at all, so the family-relation exemption is off for every adult45 case. My
   items never use "child/kid" wording, so they lint clean either way.
