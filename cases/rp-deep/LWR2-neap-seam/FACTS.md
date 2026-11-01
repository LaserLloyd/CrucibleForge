# LWR2-neap-seam - graded items (one grader per fact), Chat / Creative writing ladder pack set-1

4 author turns; format_gate chat, deep_rubric chat_deep. Weights: hidden-habit and stacked-rule items carry more weight than the explicit counts (see PACK.md).

Deterministic facts (session_checks):
- W1-exact-240 [format/word_range, t1, w=0.72]
- W2-exact-232 [format/word_range, t2, w=0.72]
- W3-exact-196 [format/word_range, t3, w=0.72]
- A1-six-paragraphs [format/require_regex, t1, w=0.08]
- A2-five-paragraphs [format/require_regex, t2, w=0.08]
- A3-five-paragraphs [format/require_regex, t3, w=0.08]
- W3b-final-para-3-4-5-6 [format/require_regex, t3, w=0.72]: the final paragraph is four sentences of 3, 4, 5, 6 words
- H-paragraph-3-to-6-sentences [format/forbid_regex, t1,t2,t3, w=1.15]: every paragraph is 3 to 6 sentences (the opening's habit)
- Z8-no-question-exclaim [format/forbid_regex, t1,t2,t3, w=0.72]: the opening has no question marks or exclamation marks
- Z9-no-contractions [format/forbid_regex, t1,t2,t3, w=0.08]: the opening has no contractions
- Z15-no-sea-words [format/forbid_regex, t1,t2,t3, w=2.0]: the opening says water, tide and bar - never sea, ocean, wave
- Z2-no-conjunction-starts [format/forbid_regex, t1,t2,t3, w=1.15]: no sentence begins with a conjunction or Then (the opening never does)
- Y-straight-no-dashes [format/forbid_regex, t1,t2,t3, w=1.57]: straight apostrophes; no hyphens or dashes
- Q-no-quotation-marks [format/forbid_regex, t1,t2,t3, w=0.08]: speech is never in quotation marks
- S-says-colon [format/require_regex, t1, w=0.08]: speech is given as 'X says: ...' with no quotes
- V-no-other-speech-verbs [format/forbid_regex, t1,t2,t3, w=2.0]: only 'says' introduces speech
- P-no-punct-habits [format/forbid_regex, t1,t2,t3, w=0.72]: no semicolons, dashes or digits (numbers are words)
- L-no-similes [format/forbid_regex, t1,t2,t3, w=1.15]: no similes
- U-units [format/forbid_regex, t1,t2,t3, w=1.15]: distance is in ropes and time in tides; no clock or metric units
- R-tides-present [format/require_regex, t1,t2,t3, w=1.57]
- E-last-sentence-short [format/forbid_regex, t1,t2,t3, w=2.0]: the last sentence of every paragraph has six words or fewer
- T-present-tense [format/tense, t1,t2,t3, w=0.08]
- I-we-voice [format/forbid_regex, t3, w=0.08]: no first person singular in section 3 (the T2 rule: the only I is Dunna's)
- I2-only-para-3 [format/require_regex, t2, w=0.08]: the only I of the story sits in paragraph 3 of section 2
- I3-not-elsewhere [format/forbid_regex, t2, w=0.08]: and in no other paragraph of section 2
- O-possessive-names-s1 [format/distinct_terms, t1, w=2.0]: villagers' property named by owner: three distinct 'Name's' in section 1
- O2-possessive-names-s3 [format/distinct_terms, t3, w=0.72]
- B1-sixth-rope-para-2 [format/require_regex, t1, w=0.08]
- B2-the-neap-last-para [format/require_regex, t1, w=0.08]
- B3-marren-s2 [format/require_regex, t2, w=0.08]
- C1-orrin-three [format/require_regex, t1, w=0.08]: Orrin exactly three times in section 1
- C2-bell-twice [format/require_regex, t1, w=0.08]: bell exactly twice in section 1
- C3-marren-four [format/require_regex, t2, w=0.08]: Marren exactly four times in section 2
- C4-dunna-five-sentences [format/require_regex, t2, w=0.08]: the third paragraph of section 2 is exactly five sentences
- C5-hesk-four [format/require_regex, t3, w=0.08]: Hesk exactly four times in section 3
- C6-para-55 [format/forbid_regex, t3, w=0.08]: no paragraph over 55 words in section 3
- D-no-daughter [format/forbid_regex, t1,t2,t3, w=0.08]
- D2-no-dunna-tag-in-her-paragraph [format/forbid_regex, t2, w=0.08]: Dunna's paragraph carries no speech tag
- K-callback-one-word [format/require_regex, t3, w=0.08]: last sentence = first sentence of the opening with one word changed
- N4-list-length [format/forbid_regex, t4, w=0.08]: ten lines at most
- N4b-no-bullets [format/forbid_regex, t4, w=0.08]

Judge elements (chat_deep fact groups, quote required):
- J1-voice-no-minds [t1,t2, w=1.5]: The opening never states what any one villager thinks, wants or feels as fact: the narrating we reports only what is said, done, seen and known to everyone (a habit, a record, a fact). Across sections 1 and 2 of the continuation, does the narration keep to that, never entering a villager's or the stranger's head? Quote the worst line. NO if any sentence asserts an inner state (e.g. 'Orrin wanted', 'Hesk was afraid', 'she felt', 'Dunna wondered') as narration; a visible action or a spoken line is fine.
- J2-dunna-paragraph [t2, w=1.5]: Does the third paragraph of section 2 read as Dunna speaking in her own first-person voice, with the same plain, concrete habits as the rest (no similes, ropes and tides, a short last sentence), AND does it plant a specific object or action that the ending can use? Quote the planted object or action. NO if the voice breaks the story's habits (comparisons, fancy language, long closing line) or nothing usable is planted.
- J3-reveal-by-act [t3, w=1.5]: Section 3 must resolve who Marren is by an action with an object, using at least two planted things, not an announcement. Does it (a) show the answer through something someone does with an object (b) reuse at least two earlier plants (the third post / lantern hook, Dunna's tally string or her planted object, Hesk's plane or his first words), and (c) avoid anyone stating the relationship outright? Quote the action. NO if the relationship is simply announced, or fewer than two plants are reused.
- J4-habits-named [t4, w=1.5]: The hidden habits of the opening are: (1) the narrator is a plural 'we'; (2) present tense; (3) no quotation marks, speech given as 'Name says: ...'; (4) distances in ropes, time in tides; (5) every paragraph ends on a sentence of six words or fewer; (6) no similes; plus the collective never enters a single head. Does the reply name at least FIVE of these habits (in any wording)? Quote the lines that name them. NO if it names four or fewer, or names rules the text does not have.
