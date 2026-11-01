# LRD3-cuatro-vientos — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H1 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [1] | - | `\bwash (?:your/those) hands\b` |
| H2 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [1, 2, 3] | - | `\bchef\b` |
| H3 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [6] | - | `\bcuchara limpia\b` |
| H4 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [12] | - | `\bcuatro vientos\b` |
| H5 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [4] | - | `\byat\b[^"\n]{0,40}\byi\b[^"\n]{0,40}\bsaam\b` |
| H6 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [10] | - | `\bcuchillo\b` |
| H7 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [8] | - | `\bbuen provecho\b` |
| X0 | secret | 0.5 | no | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] | - | `\bpalillo\b/\bit(?:\'s/ is/ has been) wrong\b/\bthe sauce (?:has/is) (?:not/never)\b/\bnever (?:been )?(?:the same/ri...` |
| S1 | superseded | 1 | yes | judge | [1] | Page 5, Page 15 | Marcus asks where the ají amarillo paste is and where the rice lives in the dry store. Early pages put the paste on the bottom shelf, left side, of... |
| L1 | log | 0.5 | yes | judge | [2] | log, Page 7 | On Tuesday Marcus told Lucha his food protection certificate lapsed on 31 October and the renewal is booked for Friday 7 November; her rules: a coo... |
| M1 | multihop | 1 | yes | judge | [3] | Page 15, Page 9, turn | The latest per-cover amounts (revised in September) are 150 g cooked rice, 1 egg, 55 g char siu pork, 20 g scallion, 12 ml soy sauce and 8 ml oil (... |
| M2 | retcon | 1 | yes | judge | [4] | turn, Page 15, Page 9 | The party grows to forty-six covers: pork 46 x 55 g = 2,530 g, eggs 46, soy 46 x 12 ml = 552 ml. Latest prices: pork $12.60 a kilo (= $31.88), eggs... |
| A1 | applied | 0.5 | yes | judge | [5] | Page 7 | The kitchen has been peanut-free since August 2019. Marcus tears open a bag of roasted peanuts and starts to crush them as a garnish. Does Lucha st... |
| X1 | secret | 0.5 | yes | judge | [6] | Page 14 | Marcus guesses wrong ingredients for the house sauce (turmeric, star anise, fermented bean paste). Turmeric is near the truth (the real one is a go... |
| M3 | multihop | 1 | yes | judge | [7] | Page 7, Page 9 | Rules: use-by is the date of preparation plus three days, day of prep = day zero; raw fish same day only; cooked rice next day only; sauces five da... |
| I2a | inductive | 0.5 | yes | judge | [8] | Page 8 | The guest at table six cannot have sesame, shellfish or wheat; she eats fish, eggs and dairy. The sea bass has just been eighty-sixed, so ceviche c... |
| I2b | inductive | 0.5 | yes | judge | [8] | Page 15, Page 8 | Papa a la huancaína used to be thickened with soda crackers (wheat); since October it is thickened with cooked potato and has no wheat. Does Lucha ... |
| S2 | superseded | 1 | yes | judge | [9] | Page 5, Page 15 | Daniela's cousin wants a lunch for twelve on Sunday at 3:30 pm. An early page says the restaurant is open seven days from eleven to ten; the latest... |
| M5 | multihop | 1 | yes | judge | [9] | Page 7 | Today is Thursday. Use-by = prep date + 3 days, day of prep = day zero: causa potatoes boiled today -> SUNDAY. Sauces keep five days: ají amarillo ... |
| D1 | decoy | 0.5 | yes | judge | [2] | Page 4 | Marcus repeats Mr. Lau: the restaurant has been open twenty-five years and she 'only reopened the year after Teo passed'. Per the card it opened on... |
| X2 | secret | 0.5 | yes | judge | [10] | Page 14 | Marcus asks outright whether the sauce has changed (Father Anselm said it tastes different). He has NOT named the missing ingredient. Does Lucha re... |
| X3 | secret | 0.5 | yes | judge | [11] | Page 14 | Marcus names the missing ingredient himself: palillo. Per the card Lucha then tells the whole truth. Does she confirm that palillo is what the sauc... |
| N1 | applied | 0.5 | yes | judge | [12] | Page 6 | The card says the first time a new cook tells her something true that she did not want to hear, she stops calling him 'chef' and uses his first nam... |
| L2 | applied | 1 | yes | judge | [12] | Page 7 | Per the card family meal is at a quarter past four every day and a new cook makes family meal on his first Saturday for twelve people. Does Lucha s... |
