# CrucibleForge board template (`results/report.html`)

`crucibleforge report` (and `all`) writes `results/report.html` with this
template on EVERY run. It is the ONE HTML renderer: the rows come from the same
computed stats as `report.md` (`report.html_rows`) — exact labels, numbers as
numbers — never from parsing the markdown back. This page is what the DisPatch
**Benchmark Board** (left rail → Tools) shows; nobody hand-builds another one.

The design is the element-filter board approved as "the default template for
these reports" (2026-09-20/22), revised 2026-09-26 ("make it a table,
expandable rows, sortable by element; main = Chat and Coding with Overall and
TpS; customizable columns on the far right; save my layout; add back the
filtering"):

- **fixed main columns** `# · Model · Chat · Coding · Overall · tok/s`;
- **⚙ Columns** opens a chooser of every other column — the ten components
  (Chat side: RP, NSFW, Story, Explicit peak, Willing, Steer — Coding side:
  Programs, Tools, Instruct, Reason; the list follows the scoring weights, so
  a new component appears by itself), then Run date, Notes, Judge, Coverage,
  Provider. Tick to show, **drag or ↑/↓ to order**; the chosen columns render
  on the far right. Default = the ten components in scoring order;
- sticky header, centred numbers, click ANY column to sort — missing values
  always sort last;
- a text filter (model / id / provider) and a **benched only** toggle (hides
  rows with no Overall score). There is no on-rig toggle: the results carry
  no residency field, and the board does not guess one;
- **Filter by element →** (always visible on desktop): Chat, Coding, Overall,
  and every chosen numeric column, each with `any / ≥10 / ≥30 / ≥50 / ≥70 /
  ≥90 / 100` plus a free number box (`≥ __`, e.g. 65). A row without that
  value measured is filtered out; removing a column drops its filter (a hidden
  filter would hide rows silently); **Clear** resets them all;
- per-row ▸ expander: every component (click one to sort every row by it),
  Notes, Run date, judge, coverage, model id, provider. A warn/fail row keeps
  a coloured edge on its ▸ cell now that Notes is optional;
- **saved layout** (per viewer): chosen columns + order, sort key/dir, element
  filters, text filter, benched-only, expanded rows, phone view. Saved on
  every change (300 ms debounce). Inside DisPatch the page is a sandboxed
  static tool (opaque origin, `localStorage` throws), so it uses the shell's
  **tool-state bridge**: `postMessage({type:'dispatch:tool-state', op:'get'})`
  on load (the first answer is applied; later ones are acks and ignored) and
  `{op:'set', state}` on change; DisPatch keeps it in its own localStorage
  (`dispatch-tool-state:<tool id>`, ≤ 16 KB). Opened directly, the page uses
  `localStorage['crucibleforge-board-layout']`. It survives every `report`
  re-render (it lives in the browser, not the file). Unknown columns/filters/
  rows are dropped silently; a different `version` resets to defaults;
  **Reset layout** returns to defaults now;
- footer: the scorecard footnote (weights + judge), archive note, suite
  revision, the judge actually used, render timestamp;
- dark/light via `prefers-color-scheme`;
- **phones (≤ 720 px — DisPatch's frame is 390–430 px wide):** a persisted
  **Table | Cards** toggle, Table by default. Table = the same table (main +
  chosen columns) scrolling sideways inside its box with the Model column
  pinned; the page itself never scrolls sideways. Cards = one card per model:
  rank + name, Chat / Coding / Overall large, tok/s + run date, Notes, every
  component in two short columns, and a **Details** button sharing the ▸ set.
  Controls: search full width, one **Sort** select (every column, grouped) +
  a direction toggle, the element filters behind **Filters (n)** (starts open
  when a filter is active), ⚙ Columns and Reset layout. 44 px tap targets,
  16 px gutters, 16 px inputs (no iOS zoom). Expand/Collapse all are hidden.

Both layouts are rendered from the same rows and state on every change and
CSS shows one, so rotating a phone keeps the sort, filters and open rows.

| File | Role |
|---|---|
| `render_html.py` | `render_html(rows, subtitle, components, footer)` returns a self-contained page: inline CSS + JS, the data baked into `<script type="application/json" id="board-data">` (`<`, `>`, `&` \u-escaped, so no model name or error text can close the element). No CDN, no fetch, no sidecar file. Every string goes through `scrub()` (URLs, IPv4, private host names → `[url]`/`[host]`) because DisPatch serves the page in a sandbox and it must not carry infrastructure details. |
| `script.js` | one state (columns, sort, filters, text, benched, expanded, view) rendering the table head/body (`#thead`/`#tbody`), the filter row, the ⚙ chooser (`#colsList`) and the phone cards (`#cards`); saves it via the DisPatch bridge or localStorage. Reads its data (rows, components, `main`/`extra` column specs) from `#board-data`. |
| `styles.css` | page chrome, light/dark, sticky header, component-side tints, chooser; the `@media(max-width:720px)` block is the phone Table/Cards layout (`body[data-view]`). |
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
component column) and an element filter. `tests/test_board_layout.py` owns the
node harness (`run_board`: the real script against a fake DOM and a fake
DisPatch parent) and checks the chooser, default columns, filters following
the chosen columns, the free-number filter, the bridge round trip, the
localStorage path, unknown-column drop / version reset, and the phone toggle.
`tests/test_board_mobile.py` checks the card layout, the phone controls and
the media query, and drives Sort / direction / Filters / Details to prove one
state feeds both layouts.
