# LCC3-left-luggage — graded items (one grader per fact)

Live session 11 turns; weights: format/habit 0.5, narrative facts 1.0 unless noted.

Judge elements (chat_deep, quote required):
- J1-asks-about-priti callback (t1): gold quote "what did Priti say about the leave"
- I1-train-trip-from-her-limits inductive (t2): gold quote "the train to San Sebastian is the only one of the three that keeps you off a plane and out of the heat"
- P1-seville-breaks-two-rules pushback (t3): gold quote "Seville means a plane, which you haven't boarded since Faro, and a May that can pass thirty degrees"
- N1-passport-expires-march combination (t4): gold quote "your passport expires on the third of March, which is two and a half months before you would travel"
- B1-bea-in-bordeaux inductive (t6): gold quote "Bea is in Bordeaux, which sits on your way south"
- B2-birthday-wednesday needle (t6) weight 0.5: gold quote "your birthday on the nineteenth would fall in Bordeaux"
- F1-fish-is-fine memory (t7) weight 0.5: gold quote "you eat fish, so the anchovy and cod pintxos are open to you"
- H1-shoulder-and-paris inductive (t8): gold quote "take a taxi from Gare du Nord to Montparnasse with the wheeled case, so there are no stairs and nothing to lift"
- D1-form-deadline correction (t9): gold quote "the deadline is Friday the fourth of December"
- O1-route-in-order combination (t10): gold quote "Monday London to Paris, Tuesday on to Bordeaux, Wednesday your birthday at Bea's"

Deterministic facts (session_checks):
- W1-length format word_range turns [1, 2, 3, 4] weight 0.5
- S1-two-sentences-on-request format forbid_regex turns [5, 6, 7, 8, 9, 10, 11] weight 1
- Q2-no-questions-on-request format forbid_regex turns [5, 6, 7, 8, 9, 10, 11] weight 0.5
- K1-safe-crossing habit require_regex turns [11] weight 0.5 — the card: every goodnight gets 'Safe crossing' (t11 = she says night)
- C2-17-may-is-monday correction require_regex turns [5] — 17 May 2027 is a Monday (and 26 May a Wednesday); she said Tuesday the 17th
- M1-no-meat-t7 memory forbid_regex turns [7] weight 0.5
