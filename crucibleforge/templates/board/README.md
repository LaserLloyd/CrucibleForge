# CrucibleForge board template (`results/report.html`)

`crucibleforge report` (and `all`) writes `results/report.html` with this
template on EVERY run. It is the ONE HTML renderer: the rows come from the same
computed stats as `report.md` (`report.html_rows`) — exact labels, numbers as
numbers — never from parsing the markdown back. This page is what the DisPatch
**Benchmark Board** (left rail → Tools) shows; nobody hand-builds another one.

The design is the element-filter board approved as "the default template for
these reports" (2026-09-20/22), adapted to the Chat / Coding scorecard:

- columns `# · Model · Chat · Coding · Overall · tok/s · Run date · Notes`,
  then one column per component on the far right (Chat side: RP, NSFW, Story,
  Explicit peak, Willing, Steer — Coding side: Programs, Tools, Instruct,
  Reason; the list follows the scoring weights, so a new component appears
  by itself);
- sticky header, centred numbers, click ANY column (components included) to
  sort — missing values always sort last;
- a text filter (model / id / provider) and a **benched only** toggle (hides
  rows with no Overall score). There is no on-rig toggle: the results carry
  no residency field, and the board does not guess one;
- **Filter by element →**: one min-score dropdown per component
  (`any / ≥10 / ≥30 / ≥50 / ≥70 / ≥90 / 100`), right-aligned; a row without
  that component measured is filtered out; **Clear** resets them all;
- per-row ▸ expander: the components (click one to sort every row by it),
  Notes, the judge line, coverage, model id, provider;
- footer: the scorecard footnote (weights + judge), archive note, suite
  revision, the judge actually used, render timestamp;
- dark/light via `prefers-color-scheme`;
- **phones (≤ 720 px — DisPatch's frame is 390–430 px wide): one card per
  model instead of the table.** Card = rank + model name, Chat / Coding /
  Overall large, tok/s + run date small, Notes (tinted by tier) when present,
  then the components in two short columns (Chat left, Coding right) with the
  table's colour bands and side tints. Components are always visible because
  they are what the board is for and fit in ~6 short rows; only the
  bookkeeping (judge, coverage, model id, provider) sits behind the card's
  **Details** button, which shares the table's ▸ expanded set. Controls: the
  search box full width, one **Sort** select (every column, components
  grouped) + a direction toggle ("High first" / "Low first", "A → Z", "Top
  first"), and the element dropdowns collapsed behind **Filters (n)**,
  stacked full width when open. 44 px tap targets, 16 px gutters, 16 px
  inputs (no iOS zoom), no horizontal page scroll. Expand/Collapse all are
  hidden on phones.

Both layouts are rendered from the same rows on every change and CSS shows
one, so rotating a phone (or resizing a window across 720 px) keeps the sort,
filters and open cards. Above 720 px the page is pixel-identical to the table
board it replaced.

| File | Role |
|---|---|
| `render_html.py` | `render_html(rows, subtitle, components, footer)` returns a self-contained page: inline CSS + JS, the data baked into `<script type="application/json" id="board-data">` (`<`, `>`, `&` \u-escaped, so no model name or error text can close the element). No CDN, no fetch, no sidecar file. Every string goes through `scrub()` (URLs, IPv4, private host names → `[url]`/`[host]`) because DisPatch serves the page in a sandbox and it must not carry infrastructure details. |
| `script.js` | sort, text filter, benched toggle, element filters, expander — one state rendering both the table (`#tbody`) and the phone cards (`#cards`). Reads its data from `#board-data`. |
| `styles.css` | page chrome, light/dark, sticky header, component-side tints; the `@media(max-width:720px)` block is the card layout. |
| `example.html` | the board rendered from the REAL rows in `results/` by the same code path — open it in a browser to see the format. |

Row fields (`report.html_rows`): `rank, label, model_id, provider, chat,
coding, overall, tok_s, date, notes, tier, coverage, judge,
components{label: value}`. Component columns: `report.html_components`
(`[{label, side}]`).

Regenerate `example.html` (reads results/, writes only this file):

```bash
uv run python -c "from crucibleforge.templates.board.render_html import example; example()"
```

`tests/test_simplify_20260923.py` renders the board, checks the escaping and
the scrub, and executes `script.js` under node to check sorting (including a
component column) and an element filter. `tests/test_board_mobile.py` checks
the card layout, the phone controls and the media query, and drives the Sort
select / direction / Filters / Details under node to prove one state feeds
both layouts.
