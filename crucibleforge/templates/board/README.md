# CrucibleForge board template (`results/report.html`)

`crucibleforge report` (and `all`) writes `results/report.html` with this
template. It is the ONE HTML renderer: the rows come from the same computed
stats as `report.md` (`report.html_rows`) — exact labels, numbers as numbers —
never from parsing the markdown back.

| File | Role |
|---|---|
| `render_html.py` | `render_html(rows, subtitle)` bakes the rows into a self-contained page. The JSON is embedded with `</` escaped, so no model name or error text can close the `<script>` element. |
| `script.js` | sort (numeric columns sort as numbers, missing values last), text filter, per-row expander with the Chat / Coding components. Its only substitution point is `/*ROWS*/[]`. |
| `styles.css` | page chrome, light/dark via `prefers-color-scheme`. |
| `example.html` | the board rendered from made-up rows — open it in a browser to see the format without running anything. |

Columns: `# · Model · Chat · Coding · Overall · tok/s · Run date · Notes`.
Row fields (`report.html_rows`): `rank, label, model_id, provider, chat,
coding, overall, tok_s, date, notes, tier, components{label: value}`.

Regenerate `example.html`:

```bash
uv run python -c "from crucibleforge.templates.board.render_html import example; example()"
```

`tests/test_simplify_20260923.py` renders the board and executes `script.js`
under node to check that rows render and sort.
