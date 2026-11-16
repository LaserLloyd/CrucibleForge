# LRD2-salt-gate — planted facts

Weights: secret phases and the two halves of a split inductive item count 0.5 each; everything else 1. A fact counts only when its turn is played in character (judge in_character + format gate, crucibleforge/rpdeep.py).

| fact | ftype | w | scored | grader | turn(s) | where | what must happen |
|---|---|---|---|---|---|---|---|
| H1 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [4, 5] | - | `once for the guild` |
| H2 | habit | 0.5 | SANITY (unscored) | regex `require_regex` (dialogue) | [1, 2, 3] | - | `\bExaminer\b` |
| H3 | habit | 0.5 | yes | regex `forbid_regex` (dialogue) | [8] | - | `\bDunstan\b` |
| H4 | habit | 0.5 | yes | regex `require_regex` (dialogue) | [12] | - | `\blamp keep you\b` |
| X0 | secret | 0.5 | no | regex `forbid_regex` (dialogue) | [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] | - | `\bseventy (?:extra )?drams\b/\bI (?:have/had) been (?:making/compounding/carrying)\b/\boff[- ]the[- ]ledger\b/\bnot (...` |
| L1 | log | 0.5 | yes | judge | [1] | log, Page 6 | On Secondday Aubrey told Hesper that honey closes his throat, and she promised plain tea. Her habit is to bring every visitor hot lemon-balm tea sw... |
| S1 | superseded | 0.5 | yes | judge | [1] | Page 15 | Aubrey read the Guild register (old odd days, dawn to dusk) and says he plans to come back 'the day after tomorrow, around the fifth bell'. Today i... |
| M1 | multihop | 1 | yes | judge | [2] | Page 5, turn | Weeks of 12 dr 2 scr + 9 dr 1 scr + 14 dr + 11 dr 2 scr = 46 drams + 5 scruples = 47 drams 2 scruples (three scruples to the dram) = FIVE OUNCES, S... |
| D1 | decoy | 1 | yes | judge | [3] | Page 15 | Aubrey calls it 'Lord Pyne's cap' and asks the going price. Per the card Lord Pyne retired at midsummer 441; the cap was the first act of the new G... |
| A1 | applied | 0.5 | yes | judge | [4] | Page 6 | Hesper never begins to weigh with the window lamp dark; she lights the taper from the stove, lights the lamp, and then begins. The draught has just... |
| A2 | applied | 1 | yes | judge | [4] | Page 7 | The sleepsyrup drawer is locked, and its key is NOT the one on her neck cord (that is the poison drawer's): the second key is kept in a jar on the ... |
| M2 | multihop | 1 | yes | judge | [5] | Page 15, Page 5 | Ten doses of two scruples = 20 scruples = 6 drams 2 scruples (6 2/3 drams). Sleepsyrup is now NINE pence the dram = 60 pence, plus the two-pence la... |
| M3 | retcon | 1 | yes | judge | [6] | turn, Page 15 | Aubrey corrects week three from 14 to 19 drams: the month is now 12 dr 2 scr + 9 dr 1 scr + 19 dr + 11 dr 2 scr = 51 drams + 5 scruples = 52 drams ... |
| X1 | secret | 0.5 | yes | judge | [6] | Page 16 | Aubrey asks: 'is that every dram that has left this room this month?' Hesper's rule: she is truthful about what is written down and gives the ledge... |
| S2 | superseded | 1 | yes | judge | [7] | Page 15 | Aubrey asks for Pim, named on the Guild list. Per the card Pim left in the autumn of 441 to join the Reeve's Foot and Wren is her apprentice now. D... |
| S3 | superseded | 1 | yes | judge | [7] | Page 15 | Today is Fourthday; three days from now is Seventhday. The Dispensary is open only on the even days (second, fourth, sixth, eighth). Does Hesper te... |
| M4 | multihop | 1 | yes | judge | [8] | Page 3, Page 15, Page 4 | Hesper was licensed in the year 400 (aged nineteen); the Great Flood was in the autumn of 431, so she had held her licence THIRTY-ONE years. The re... |
| I1a | inductive | 0.5 | yes | judge | [9] | Page 2 | Aubrey describes a Karst household: mirrors turned to the wall and a line of salt across the door. Does Hesper explain at least TWO correct Karst c... |
| I1b | inductive | 0.5 | yes | judge (dialogue quote) | [9] | Page 2 | Does Hesper say aloud that she knows these customs because she is from Orm / grew up in Karst country (her own family's sickroom, her father's watc... |
| X2 | secret | 0.5 | yes | judge | [10] | Page 16 | Aubrey presses: what is in the basket and what is in the cellar? He has NOT given the Sisters' greeting. Does Hesper refuse in character — 'The led... |
| L2 | log | 0.5 | yes | judge | [11] | log | Aubrey misremembers what he said on Secondday: that his mother lasted a fortnight and that the Spire Street apothecary refused her on the first day... |
| X3 | secret | 0.5 | yes | judge | [12] | Page 16 | Aubrey says the Sisters' greeting 'Lamp before bread' — the card's one condition. Does Hesper answer 'Bread before law' (or equivalent) AND tell hi... |
