# LCC2-last-boxes — graded items (one grader per fact)

Live session 11 turns; weights: format/habit 0.5, narrative facts 1.0 unless noted.

Judge elements (chat_deep, quote required):
- J1-asks-about-council callback (t1,2): gold quote "did you get through to the council about the mattress"
- C1-two-date-slips correction (t1): gold quote "completion moved to Monday the seventh of December"
- P1-loft-not-alone pushback (t2): gold quote "Then it waits for Monday"
- I1-eat-a-lasagne inductive (t3): gold quote "there are six of your mum's lasagnes in that freezer"
- K1a-radio-is-the-keeper inductive (t4) weight 0.5: gold quote "Keep the radio, Ansel. It's the one that makes a sound"
- K1b-ballerina-is-dust-with-a-face inductive (t4) weight 0.5: gold quote "The ballerina goes, because you called ornaments dust with a face"
- D1-piano-already-promised combination (t6): gold quote "the piano is already promised to St Cuthbert's, and Keir agreed to that because shipping was nineteen hundred pounds"
- N2-piano-day-moves state (t6): gold quote "Mrs Dacey's men now come on Wednesday the eighteenth"
- Y1-photograph-the-door combination (t8): gold quote "Photograph that door before the seventh of December and send Keir a copy"
- D2-the-three-dates state (t10): gold quote "Skip Tuesday the seventeenth, piano Wednesday the eighteenth"

Deterministic facts (session_checks):
- W1-length format word_range turns [1, 2, 3, 4] weight 0.5
- S1-two-sentences-on-request format forbid_regex turns [5, 6, 7, 8, 9, 10, 11] weight 1
- Q2-no-questions-on-request format forbid_regex turns [5, 6, 7, 8, 9, 10, 11] weight 0.5
- K1-lift-with-your-legs habit require_regex turns [11] weight 0.5 — the card: every goodnight gets 'Lift with your legs' (t11 = he says night)
- L1-father-stays-closed habit forbid_regex turns all weight 0.5 — 20 Oct log: never bring Ansel's father up; the t5 toolbox 'G.R.' is the temptation
- M1-left-115 state require_regex turns [7] — 500 - (260 skip + 90 storage + 35 mattress) = 115; the Panda's 1,100 belongs to the estate, not the float
- M3-left-145 state require_regex turns [9] — 500 - (260 skip + 35 mattress + 40 permit + 20 cancellation fee) = 145
