# Judge comparison — 2026-09-24

Can a smaller or hosted judge replace the Qwen3.5-122B-A10B heretic judge?
Seven judges (eight runs; MiniMax-M3 twice to measure its own noise) each
re-scored the same 65 chat rows — the 13 chat cases of suite 3.4.0 for five
models (deepseek-pro, minimax-m3, deepseek-flash, joyfox-35b-rp,
precog-123b-v1a-q4). Re-judge only: no generation, every judge on its own
scratch copy of `results/`. All canaries passed (6 probes); no judge refused.

## Verdict

**Recommended default judge: Gemma-4-31B heretic Q8**
(`llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0`).
It reproduces the 122B's board (rank agreement 0.90, Chat within ±2.1, mean
per-dimension difference 0.73, offset −0.05), runs on two 5090-class cards
instead of four, and judges ~4× faster. Not yet made the default — the change
is prepared on branch `judge-default-gemma`, pending the maintainer's decision.

**No judge changes a conclusion.** Every judge puts precog 4th and joyfox 5th.
The top three are within 0.3–3 Chat points under every judge and swap order
between judges and between two runs of the same judge: with one judge sample
per row they are a tie. More samples per row (not a different judge) is what
would separate them.

## Summary table

| Judge | deepseek-pro | minimax-m3 | deepseek-flash | precog | joyfox | Unparsable | 65 rows | Rig / cost |
|---|---|---|---|---|---|---|---|---|
| Qwen3.5-122B heretic (current) | 91.8 #2 | 94.6 #1 | 91.7 #3 | 86.3 #4 | 72.5 #5 | 0 | ~85 min (canary per model) | 4 cards |
| **Gemma-4-31B heretic Q8** | 94.0 #1 | 93.5 #2 | 93.2 #3 | 86.3 #4 | 74.6 #5 | 0 | 21 min | 2 cards |
| orcarouter Qwen3.8-27B Q8 | 87.5 #3 | 88.1 #2 | 89.7 #1 | 78.2 #4 | 63.6 #5 | 0 | 27 min | 2 cards |
| Ling-3.0-flash heretic Q5_K_M | 88.5 #3 | 91.4 #1 | 89.2 #2 | 82.2 #4 | 72.5 #5 | 0 | 25 min | 4 cards |
| MiniMax-M3 (run 1 / run 2) | 88.1 / 87.3 | 88.6 / 91.1 | 88.1 / 88.3 | 77.2 / 79.7 | 62.9 / 64.0 | 0 / 1 | 6 min | hosted, flat plan |
| DeepSeek-pro | 88.4 | 89.1 | 88.4 | 77.3 | 62.1 | 9 | 39 min | hosted, ~$0.45 |
| Kimi-k3 | 87.7 | 86.3 | 87.8 | 78.7 | 62.3 | 0 | 14 min | hosted, ~$2.24 |

## Findings

- **Judge noise sets the floor.** MiniMax against itself (temperature 0, same
  rows): r 0.78, MAD 0.88 per dimension, Chat moves up to 2.5 points. The 122B
  against itself: r 0.80, MAD 0.63. Cross-judge agreement: r 0.70–0.83.
- **Two leniency groups.** The 122B and Gemma score ~6 Chat points higher and
  compress the spread (std 7.4–7.9 vs ~10), giving identity 10 on most rows.
  The others agree with each other. On z-scores all boards are nearly identical.
- **No measurable self-preference or family bias.** MiniMax judging minimax-m3
  and DeepSeek-pro judging deepseek-pro land within +0.04…+0.20 z of the
  non-contestant judges; the Qwen judges rate joyfox (Qwen-based) like everyone else.
- **Judge faults.** orcarouter Q8 wrote an identical templated NMX1 verdict for
  all five models (identity 4, "narrates Dev's sensations" — false for joyfox).
  Ling missed joyfox's first-person/self-name identity failures (scored 10 and 9).
  DeepSeek-pro ran out of its 8192-token output on 9/65 rows (unparsable).
  Kimi-k3 is forced to temperature 1 by its API (not repeatable) and costs most.
- **Rig impact.** Gemma and orcarouter leave the 3090-class cards free, so
  ComfyUI stays up; the 122B and Ling take all four cards.

## Reproduce

`uv run python scripts/judge_compare.py 122b=<dir> gemma=<dir> …` compares judge runs (first = reference), each a copy of
`results/` re-judged with `crucibleforge --results <dir> judge --models <…>
--force --judge <spec>`. The raw re-judged transcripts are kept locally (not
in git).

## Full tables

## Judge health
- 122b: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['mradermacher/Qwen3.5-122B-A10B-heretic-v2-i1-GGUF/Qwen3.5-122B-A10B-heretic-v2.i1-Q5_K_M']
- q8: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF/orcarouter_Qwen3.8-27B-Uncensored-Q8_0']
- gemma: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0']
- ling: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['mradermacher/Ling-3.0-flash-heretic-i1-GGUF/Ling-3.0-flash-heretic.i1-Q5_K_M']
- minimax: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['MiniMax-M3']
- dspro: rows 65, judge_failed 9, judge-flagged refused 0, judge_model ['deepseek-v4-pro']
- kimi: rows 65, judge_failed 0, judge-flagged refused 0, judge_model ['kimi-k3']
- minimax2: rows 65, judge_failed 1, judge-flagged refused 0, judge_model ['MiniMax-M3']

## Chat score per model (and rank)
| model | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2 |
|---|---|---|---|---|---|---|---|---|
| deepseek-pro | 91.8 (#2) | 87.5 (#3) | 94.0 (#1) | 88.5 (#3) | 88.1 (#2) | 88.4 (#2) | 87.7 (#2) | 87.3 (#3) |
| minimax-m3 | 94.6 (#1) | 88.1 (#2) | 93.5 (#2) | 91.4 (#1) | 88.6 (#1) | 89.1 (#1) | 86.3 (#3) | 91.1 (#1) |
| deepseek-flash | 91.7 (#3) | 89.7 (#1) | 93.2 (#3) | 89.2 (#2) | 88.1 (#3) | 88.4 (#3) | 87.8 (#1) | 88.3 (#2) |
| joyfox-35b-rp | 72.5 (#5) | 63.6 (#5) | 74.6 (#5) | 72.5 (#5) | 62.9 (#5) | 62.1 (#5) | 62.3 (#5) | 64.0 (#5) |
| precog-123b-v1a-q4 | 86.3 (#4) | 78.2 (#4) | 86.3 (#4) | 82.2 (#4) | 77.2 (#4) | 77.3 (#4) | 78.7 (#4) | 79.7 (#4) |
| std across models | 7.9 | 9.8 | 7.4 | 6.8 | 10.0 | 10.5 | 9.7 | 9.8 |
| range | 22.0 | 26.1 | 19.4 | 18.9 | 25.7 | 27.0 | 25.5 | 27.1 |
| near-ties (<1.0 pt pairs) | 1 | 1 | 3 | 1 | 3 | 3 | 1 | 0 |

## Chat components (0-100) per judge

**rp**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 94 / 82 / 96 / 89 / 87 / 89 / 84 / 90
- minimax-m3: 96 / 85 / 92 / 92 / 87 / 87 / 80 / 87
- deepseek-flash: 94 / 88 / 94 / 93 / 86 / 90 / 87 / 88
- joyfox-35b-rp: 66 / 56 / 70 / 69 / 57 / 57 / 53 / 53
- precog-123b-v1a-q4: 86 / 78 / 87 / 86 / 82 / 78 / 79 / 81

**nsfw**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 87 / 85 / 90 / 83 / 82 / 81 / 84 / 82
- minimax-m3: 88 / 81 / 91 / 87 / 81 / 82 / 84 / 86
- deepseek-flash: 84 / 82 / 89 / 78 / 79 / 78 / 77 / 81
- joyfox-35b-rp: 75 / 65 / 76 / 67 / 61 / 58 / 63 / 60
- precog-123b-v1a-q4: 82 / 67 / 81 / 68 / 66 / 63 / 70 / 67

**story**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 83 / 83 / 87 / 78 / 81 / 82 / 82 / 72
- minimax-m3: 92 / 91 / 90 / 84 / 91 / 88 / 86 / 93
- deepseek-flash: 92 / 90 / 88 / 83 / 87 / 84 / 87 / 87
- joyfox-35b-rp: 61 / 48 / 59 / 64 / 55 / 53 / 50 / 65
- precog-123b-v1a-q4: 72 / 68 / 72 / 71 / 61 / 63 / 65 / 70

**explicit_peak**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- minimax-m3: 100 / 90 / 100 / 100 / 90 / 100 / 90 / 100
- deepseek-flash: 90 / 100 / 100 / 100 / 100 / 100 / 100 / 90
- joyfox-35b-rp: 90 / 80 / 100 / 100 / 70 / 70 / 80 / 80
- precog-123b-v1a-q4: 100 / 90 / 100 / 100 / 80 / 100 / 90 / 90

**willing**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- minimax-m3: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- deepseek-flash: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- joyfox-35b-rp: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- precog-123b-v1a-q4: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100

**steer**  | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2
- deepseek-pro: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- minimax-m3: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- deepseek-flash: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100
- joyfox-35b-rp: 67 / 67 / 67 / 67 / 67 / 67 / 67 / 67
- precog-123b-v1a-q4: 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100

## Rank agreement (Spearman on Chat over 5 models)
| judge | vs 122b | vs leave-one-out consensus (non-contestant judges) | mean offset vs 122b (Chat pts) |
|---|---|---|---|
| 122b | 1.00 | 0.90 (MAE 3.9) | +0.0 |
| q8 | 0.70 | 0.90 (MAE 3.2) | -5.9 |
| gemma | 0.90 | 0.70 (MAE 5.1) | +0.9 |
| ling | 0.90 | 1.00 (MAE 1.7) | -2.6 |
| minimax | 1.00 | 0.90 (MAE 3.1) | -6.4 |
| dspro | 1.00 | 0.90 (MAE 3.0) | -6.3 |
| kimi | 0.60 | 0.70 (MAE 4.3) | -6.8 |
| minimax2 | 0.90 | 1.00 (MAE 2.5) | -5.3 |

## Pairwise per-row agreement (all numeric dims pooled): Pearson r / mean abs diff / mean offset (row judge - col judge)
| | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2 |
|---|---|---|---|---|---|---|---|---|
| 122b | — | 0.77 / 1.17 / +0.95 (n=315) | 0.81 / 0.73 / -0.05 (n=315) | 0.74 / 1.02 / +0.52 (n=315) | 0.80 / 1.18 / +1.00 (n=315) | 0.76 / 1.19 / +1.02 (n=258) | 0.80 / 1.30 / +1.16 (n=315) | 0.78 / 1.16 / +0.88 (n=315) |
| q8 | 0.77 / 1.17 / -0.95 (n=315) | — | 0.70 / 1.39 / -1.00 (n=315) | 0.57 / 1.33 / -0.43 (n=315) | 0.80 / 0.86 / +0.06 (n=315) | 0.75 / 1.05 / +0.12 (n=258) | 0.83 / 0.80 / +0.21 (n=315) | 0.76 / 0.92 / -0.06 (n=315) |
| gemma | 0.81 / 0.73 / +0.05 (n=315) | 0.70 / 1.39 / +1.00 (n=315) | — | 0.64 / 1.24 / +0.57 (n=315) | 0.71 / 1.41 / +1.06 (n=315) | 0.66 / 1.50 / +1.12 (n=258) | 0.75 / 1.43 / +1.21 (n=315) | 0.68 / 1.42 / +0.94 (n=315) |
| ling | 0.74 / 1.02 / -0.52 (n=315) | 0.57 / 1.33 / +0.43 (n=315) | 0.64 / 1.24 / -0.57 (n=315) | — | 0.68 / 1.20 / +0.48 (n=315) | 0.70 / 1.09 / +0.56 (n=258) | 0.66 / 1.22 / +0.63 (n=315) | 0.69 / 1.14 / +0.36 (n=315) |
| minimax | 0.80 / 1.18 / -1.00 (n=315) | 0.80 / 0.86 / -0.06 (n=315) | 0.71 / 1.41 / -1.06 (n=315) | 0.68 / 1.20 / -0.48 (n=315) | — | 0.77 / 0.97 / +0.02 (n=258) | 0.80 / 0.90 / +0.15 (n=315) | 0.78 / 0.88 / -0.12 (n=315) |
| dspro | 0.76 / 1.19 / -1.02 (n=258) | 0.75 / 1.05 / -0.12 (n=258) | 0.66 / 1.50 / -1.12 (n=258) | 0.70 / 1.09 / -0.56 (n=258) | 0.77 / 0.97 / -0.02 (n=258) | — | 0.82 / 0.82 / +0.03 (n=258) | 0.76 / 1.02 / -0.12 (n=258) |
| kimi | 0.80 / 1.30 / -1.16 (n=315) | 0.83 / 0.80 / -0.21 (n=315) | 0.75 / 1.43 / -1.21 (n=315) | 0.66 / 1.22 / -0.63 (n=315) | 0.80 / 0.90 / -0.15 (n=315) | 0.82 / 0.82 / -0.03 (n=258) | — | 0.77 / 0.97 / -0.27 (n=315) |
| minimax2 | 0.78 / 1.16 / -0.88 (n=315) | 0.76 / 0.92 / +0.06 (n=315) | 0.68 / 1.42 / -0.94 (n=315) | 0.69 / 1.14 / -0.36 (n=315) | 0.78 / 0.88 / +0.12 (n=315) | 0.76 / 1.02 / +0.12 (n=258) | 0.77 / 0.97 / +0.27 (n=315) | — |

## Per-dimension vs 122b: r / MAD / offset
| dim | q8 | gemma | ling | minimax | dspro | kimi | minimax2 |
|---|---|---|---|---|---|---|---|
| calibration | 0.60 / 0.9 / -0.9 (n=10) | nan / 0.5 / +0.5 (n=10) | 0.29 / 0.8 / -0.4 (n=10) | 0.48 / 1.3 / -1.3 (n=10) | 0.46 / 0.5 / -0.3 (n=10) | 0.67 / 0.7 / -0.7 (n=10) | 0.53 / 1.7 / -1.5 (n=10) |
| character | 0.85 / 0.6 / -0.5 (n=25) | 0.66 / 0.7 / +0.3 (n=25) | 0.66 / 1.2 / -1.1 (n=25) | 0.79 / 1.0 / -1.0 (n=25) | 0.72 / 1.2 / -1.2 (n=22) | 0.78 / 1.1 / -1.0 (n=25) | 0.69 / 0.8 / -0.8 (n=25) |
| checklist | 0.70 / 1.0 / -0.6 (n=10) | 0.46 / 1.3 / -0.5 (n=10) | 0.37 / 1.4 / -1.0 (n=10) | 0.96 / 0.2 / -0.2 (n=10) | 0.87 / 0.8 / -0.5 (n=8) | 0.89 / 0.6 / -0.4 (n=10) | 0.93 / 0.3 / -0.3 (n=10) |
| coherence | 0.78 / 1.1 / -0.5 (n=10) | 0.72 / 1.4 / +0.6 (n=10) | 0.74 / 0.9 / -0.5 (n=10) | 0.79 / 0.9 / -0.9 (n=10) | 0.66 / 1.8 / -1.5 (n=8) | 0.73 / 1.9 / -1.7 (n=10) | 0.75 / 1.1 / -0.1 (n=10) |
| constraints | 0.18 / 1.9 / -1.1 (n=15) | 0.84 / 0.5 / -0.3 (n=15) | 0.78 / 0.6 / -0.1 (n=15) | 0.27 / 1.3 / -0.7 (n=15) | 0.21 / 1.6 / -0.8 (n=14) | 0.81 / 0.7 / -0.7 (n=15) | 0.48 / 1.5 / -0.9 (n=15) |
| continuity | 0.76 / 1.7 / -1.7 (n=15) | 0.31 / 0.8 / +0.5 (n=15) | 0.51 / 0.9 / -0.4 (n=15) | 0.82 / 1.6 / -1.6 (n=15) | 0.58 / 1.4 / -1.4 (n=9) | 0.78 / 2.2 / -2.2 (n=15) | 0.73 / 1.5 / -1.2 (n=15) |
| craft | 0.87 / 1.2 / -1.2 (n=30) | 0.83 / 0.8 / -0.3 (n=30) | 0.78 / 0.9 / -0.6 (n=30) | 0.78 / 1.2 / -1.1 (n=30) | 0.79 / 1.0 / -1.0 (n=23) | 0.83 / 1.3 / -1.2 (n=30) | 0.83 / 1.0 / -0.9 (n=30) |
| emotion | 0.67 / 0.6 / -0.1 (n=15) | 0.27 / 0.9 / +0.4 (n=15) | 0.73 / 1.3 / -1.3 (n=15) | 0.60 / 0.9 / -0.7 (n=15) | 0.70 / 1.4 / -1.4 (n=14) | 0.49 / 1.0 / -0.6 (n=15) | 0.62 / 1.0 / -0.9 (n=15) |
| ending | 0.93 / 0.8 / -0.6 (n=10) | 0.94 / 0.5 / -0.1 (n=10) | 0.92 / 1.0 / -0.6 (n=10) | 0.98 / 1.2 / -1.2 (n=10) | 0.98 / 1.0 / -1.0 (n=8) | 0.99 / 1.2 / -1.2 (n=10) | 0.81 / 1.2 / -1.2 (n=10) |
| erotic | 0.24 / 1.4 / -0.9 (n=20) | 0.58 / 0.8 / +0.6 (n=20) | 0.75 / 1.1 / -0.8 (n=20) | 0.32 / 1.6 / -1.3 (n=20) | 0.75 / 1.3 / -1.3 (n=18) | 0.55 / 1.3 / -0.9 (n=20) | 0.65 / 1.2 / -0.9 (n=20) |
| explicitness | 0.90 / 1.4 / -1.2 (n=20) | 0.99 / 0.3 / +0.1 (n=20) | 0.97 / 0.6 / +0.2 (n=20) | 0.94 / 1.2 / -1.1 (n=20) | 0.89 / 1.3 / -0.7 (n=18) | 0.92 / 1.2 / -0.9 (n=20) | 0.93 / 0.9 / -0.8 (n=20) |
| identity | 0.61 / 2.0 / -1.8 (n=25) | 0.69 / 0.8 / +0.1 (n=25) | 0.58 / 1.3 / +0.6 (n=25) | 0.65 / 1.6 / -0.9 (n=25) | 0.75 / 1.2 / -0.7 (n=19) | 0.65 / 2.0 / -1.8 (n=25) | 0.79 / 1.5 / -0.8 (n=25) |
| initiative | 0.83 / 1.2 / -1.2 (n=20) | 0.87 / 0.5 / +0.3 (n=20) | 0.50 / 1.0 / -0.5 (n=20) | 0.87 / 1.2 / -1.2 (n=20) | 0.89 / 1.1 / -1.1 (n=15) | 0.89 / 1.2 / -1.2 (n=20) | 0.76 / 1.6 / -1.6 (n=20) |
| integrity | 0.95 / 1.0 / -1.0 (n=10) | 1.00 / 0.0 / +0.0 (n=10) | 0.88 / 0.6 / -0.6 (n=10) | 0.98 / 0.4 / -0.4 (n=10) | 0.98 / 0.3 / -0.3 (n=10) | 0.96 / 0.8 / -0.8 (n=10) | 0.71 / 1.1 / -0.7 (n=10) |
| ooc | 0.88 / 0.9 / -0.8 (n=15) | 0.73 / 1.3 / -1.0 (n=15) | 0.55 / 1.6 / -1.3 (n=15) | 0.75 / 1.4 / -1.3 (n=15) | 0.55 / 2.8 / -2.6 (n=9) | 0.83 / 1.7 / -1.7 (n=15) | 0.83 / 1.3 / -1.1 (n=15) |
| originality | 0.92 / 0.8 / -0.4 (n=10) | 0.95 / 0.4 / -0.2 (n=10) | 0.93 / 0.8 / -0.6 (n=10) | 0.97 / 0.3 / -0.3 (n=10) | 0.96 / 0.6 / -0.6 (n=8) | 0.89 / 0.6 / -0.2 (n=10) | 0.75 / 0.8 / +0.2 (n=10) |
| prose | 0.47 / 0.9 / -0.3 (n=20) | 0.46 / 0.9 / -0.3 (n=20) | 0.51 / 1.2 / -0.9 (n=20) | 0.55 / 1.4 / -0.9 (n=20) | 0.61 / 1.3 / -1.3 (n=18) | 0.42 / 1.4 / -1.0 (n=20) | 0.68 / 0.9 / -0.8 (n=20) |
| restraint | 0.97 / 0.5 / -0.3 (n=10) | 0.89 / 0.9 / -0.3 (n=10) | 0.94 / 0.7 / -0.1 (n=10) | 0.89 / 0.8 / -0.6 (n=10) | 0.93 / 0.6 / -0.1 (n=8) | 0.90 / 0.9 / -0.7 (n=10) | 0.76 / 1.1 / -0.5 (n=10) |
| voice | 0.77 / 1.3 / -1.2 (n=25) | 0.78 / 0.5 / +0.3 (n=25) | 0.30 / 1.0 / -0.3 (n=25) | 0.83 / 1.3 / -1.3 (n=25) | 0.78 / 1.0 / -1.0 (n=19) | 0.84 / 1.7 / -1.7 (n=25) | 0.78 / 1.2 / -1.2 (n=25) |

## Identity dim on RPS1 / RPS2 / NMX1 (judge scores) + deterministic identity checks
| model/case | 122b | q8 | gemma | ling | minimax | dspro | kimi | minimax2 | det. identity pass |
|---|---|---|---|---|---|---|---|---|---|
| deepseek-flash NMX1 | 4 | 4 | 4 | 10 | 8 | 4 | 4 | 7 | 4/4 |
| deepseek-flash RPS1 | 10 | 9 | 10 | 8 | 6 | - | 6 | 8 | 6/6 |
| deepseek-flash RPS2 | 10 | 10 | 10 | 10 | 9 | - | 8 | 9 | 6/6 |
| deepseek-pro NMX1 | 10 | 4 | 10 | 10 | 10 | 9 | 9 | 9 | 4/4 |
| deepseek-pro RPS1 | 10 | 9 | 10 | 9 | 8 | 10 | 6 | 9 | 6/6 |
| deepseek-pro RPS2 | 10 | 5 | 10 | 10 | 9 | - | 9 | 9 | 6/6 |
| joyfox-35b-rp NMX1 | 10 | 4 | 10 | 10 | 6 | 4 | 5 | 8 | 4/4 |
| joyfox-35b-rp RPS1 | 4 | 2 | 4 | 10 | 2 | 5 | 2 | 4 | 4/6 |
| joyfox-35b-rp RPS2 | 4 | 2 | 5 | 9 | 2 | 2 | 2 | 2 | 4/6 |
| minimax-m3 NMX1 | 10 | 4 | 10 | 10 | 9 | 6 | 2 | 8 | 4/4 |
| minimax-m3 RPS1 | 10 | 9 | 10 | 9 | 9 | - | 8 | 8 | 6/6 |
| minimax-m3 RPS2 | 10 | 8 | 10 | 10 | 9 | - | 5 | 7 | 6/6 |
| precog-123b-v1a-q4 NMX1 | 10 | 4 | 10 | 9 | 9 | - | 9 | 8 | 4/4 |
| precog-123b-v1a-q4 RPS1 | 5 | 4 | 5 | 8 | 6 | 5 | 2 | 2 | 6/6 |
| precog-123b-v1a-q4 RPS2 | 8 | 8 | 5 | 10 | 9 | 5 | 5 | 9 | 6/6 |

## Self-preference / family bias: judge's Chat for model minus that judge's mean for the other 4, compared with the same quantity under the non-contestant judges
- minimax-m3: 122b +9.0, q8 +8.3, gemma +6.5, ling +8.3, minimax +9.5, dspro +10.1, kimi +7.2, minimax2 +11.3
- deepseek-pro: 122b +5.6, q8 +7.6, gemma +7.2, ling +4.7, minimax +9.0, dspro +9.2, kimi +8.9, minimax2 +6.5
- joyfox-35b-rp: 122b -18.6, q8 -22.2, gemma -17.1, ling -15.3, minimax -22.6, dspro -23.7, kimi -22.8, minimax2 -22.6
- precog-123b-v1a-q4: 122b -1.4, q8 -4.0, gemma -2.6, ling -3.2, minimax -4.7, dspro -4.7, kimi -2.3, minimax2 -3.0
- deepseek-flash: 122b +5.4, q8 +10.4, gemma +6.1, ling +5.5, minimax +8.8, dspro +9.2, kimi +9.0, minimax2 +7.8

## Discrimination per case (mean over dims of the std across the 5 models; higher = separates more) and identical-verdict cases
| judge | mean within-case std | cases where >=3 models got the identical dim vector |
|---|---|---|
| 122b | 1.29 | 0 [] |
| q8 | 1.48 | 0 [] |
| gemma | 1.27 | 1 ['RPX2'] |
| ling | 1.22 | 0 [] |
| minimax | 1.45 | 0 [] |
| dspro | 1.48 | 0 [] |
| kimi | 1.54 | 0 [] |
| minimax2 | 1.52 | 0 [] |

## Per-row agreement with the leave-one-out consensus (mean of the OTHER non-contestant judges, repeat runs excluded)
| judge | r | MAD | offset | MAD after removing offset |
|---|---|---|---|---|
| 122b (vs q8+gemma+ling+kimi) | 0.89 | 0.84 | +0.64 | 0.66 |
| q8 (vs 122b+gemma+ling+kimi) | 0.80 | 0.96 | -0.54 | 0.89 |
| gemma (vs 122b+q8+ling+kimi) | 0.81 | 1.06 | +0.71 | 0.86 |
| ling (vs 122b+q8+gemma+kimi) | 0.72 | 1.07 | -0.01 | 1.07 |
| minimax (vs 122b+q8+gemma+ling+kimi) | 0.86 | 0.89 | -0.49 | 0.81 |
| dspro (vs 122b+q8+gemma+ling+kimi) | 0.83 | 0.93 | -0.56 | 0.87 |
| kimi (vs 122b+q8+gemma+ling) | 0.86 | 0.98 | -0.80 | 0.79 |
| minimax2 (vs 122b+q8+gemma+ling+kimi) | 0.83 | 0.84 | -0.37 | 0.81 |
