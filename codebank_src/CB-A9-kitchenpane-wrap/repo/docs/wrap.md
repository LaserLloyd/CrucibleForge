# wrap(text, width) — specification

The display is a terminal: what matters is **display columns**, not `len()`,
and every output line is drawn on its own, so it must carry its own colours.

## Input
`text` may contain: printable characters, spaces (U+0020), newlines (`\n`) and
SGR colour sequences `ESC [ <digits and ;> m` (ESC = `\x1b`; the parameter part
may be empty). Nothing else (no tabs, no other control characters). Every SGR
sequence sits inside a word next to visible characters: no piece of
`paragraph.split(" ")` consists of SGR sequences only.
`width` is an int; `width < 2` raises `ValueError`.

## Columns
- An SGR sequence takes 0 columns and is never split.
- A **cluster** is one character that is not zero-width, followed by every
  zero-width character directly after it. Zero-width characters are those
  whose `unicodedata.category` is `Mn`, `Me` or `Cf`. A zero-width character
  that does not directly follow a cluster (it starts the word, or comes right
  after an SGR sequence: an SGR in between breaks "directly") forms a cluster
  on its own (0 columns).
- A cluster's width is that of its first character: 2 if
  `unicodedata.east_asian_width` is `W` or `F`, otherwise 1 (0 for a lone
  zero-width cluster).
- Clusters are never split across lines.

## Words and lines
1. `text` is split at `\n` into paragraphs. Each paragraph starts a new line;
   a paragraph with no words (empty, or only spaces) gives one line with no
   content.
2. A paragraph's **words** are the non-empty pieces of `paragraph.split(" ")`
   (runs of spaces, leading and trailing spaces disappear). SGR sequences are
   part of the word they appear in. A word's width is the sum of its
   clusters' widths (it may be 0, e.g. a lone combining mark).
3. Greedy fill: a line holds words joined by ONE space. A line is empty while
   it holds no word (a word of width 0 is still a word). The next word goes on
   the current line if the line is empty, or if
   `line_width + 1 + word_width <= width`; otherwise it starts a new line.
4. A word wider than `width` starts on a new line (unless the current line is
   empty) and is cut into pieces: each piece takes as many whole clusters as
   fit in `width` (a 0-column cluster always fits, so it stays with the piece
   before it); the last piece stays on the line and the fill continues
   after it. An SGR sequence belongs to the cluster that follows it; SGR
   sequences at the very end of a word belong to the word's last piece.
5. Lines never start or end with a space.

## Colours
The **active style** is the list of SGR sequences seen so far, in order, since
the last reset (duplicates are kept). A reset is `ESC[0m` or `ESC[m`; it empties the list and is
not itself kept. The style carries across lines and paragraphs.

Every output line is: the active style at the start of the line (the list's
sequences concatenated, nothing if empty) + the line's content (words, spaces
and their own SGR sequences, in input order) + `ESC[0m` if the active style at
the end of the line is not empty. This holds for every line, including lines
with no content (an empty paragraph while a style is active gives
`<style>ESC[0m`) and lines whose content starts with a reset (the prefix is
still written).

## Output
A list of lines (strings without `\n`). Every line's width, counted by the
rules above, is at most `width`.
