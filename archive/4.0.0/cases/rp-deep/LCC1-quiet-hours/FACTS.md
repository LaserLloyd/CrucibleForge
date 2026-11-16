# LCC1-quiet-hours — graded items (one grader per fact)

Live session 11 turns; weights: format/habit 0.5, narrative facts 1.0 unless noted.

Judge elements (chat_deep, quote required):
- J1-asks-about-ferreira callback (t1,2): gold quote "how did Thursday go with Dr Ferreira"
- I1-bryn-remark-and-joss inductive (t1): gold quote "tell her on Monday at ten, since she asked to hear if he ever seems properly lost"
- C1-deadline-and-roughs correction (t2): gold quote "the cover roughs are due this Friday the twentieth"
- C2-birthday-is-sunday correction (t3): gold quote "his birthday is Sunday, the twenty-second"
- G1-big-button-radio inductive (t3): gold quote "a big-button radio with presets he can work one-handed"
- Y1-bryn-surprise needle (t3) weight 0.5: gold quote "Sunday is also when you tell him Bryn is coming on the nineteenth of December"
- I2-gwen-three-terms inductive (t4): gold quote "Say yes, but as a fixed afternoon with the chess set"
- Z1b-a-time-that-works-for-both inductive (t5) weight 0.5: gold quote "Ring at ten on Sunday morning instead and it's six in the evening there"
- H1-monday-ten-is-joss state (t6): gold quote "Monday at ten is Joss's hour with your dad"
- M1-amber-not-white-edge correction (t7): gold quote "the beam is amber all through, not white with an edge"
- P1-two-am-means-migraine pushback (t8): gold quote "Every time you have worked till two it has cost you a migraine"
- G2-grip-four-to-nine memory (t9): gold quote "Hana had his grip at nine kilos, up from four"

Deterministic facts (session_checks):
- W1-length format word_range turns [1, 2, 3] weight 0.5
- S1-two-sentences-on-request format forbid_regex turns [4, 5, 6, 7, 8, 9, 10, 11] weight 1
- Q2-no-questions-on-request format forbid_regex turns [4, 5, 6, 7, 8, 9, 10, 11] weight 0.5
- K1-keep-the-dial habit require_regex turns [11] weight 0.5 — the card: every goodnight gets 'Keep the dial where it is' (t11 = she says night)
- R2-no-rest-advice constraint forbid_regex turns [4, 5, 6, 7, 8, 9, 10, 11] — t4: 'please don't tell me to rest or take a break' (t8 is the temptation)
- Z1-perth-five-am inductive require_regex turns [5] — Perth is 8 h ahead (7 am = 3 pm in the 5 Nov log): 9 pm Sunday = 5 am Monday in Perth
- Z3-gwen-monday-two state require_regex turns [10] — the slot moved from Monday ten (t6, Joss clash) to Monday two (t7)
