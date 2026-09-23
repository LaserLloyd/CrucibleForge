# CrucibleForge board template (wired CLI default)

This directory is the **default template** that `crucibleforge report` ships with.
`crucibleforge report` calls `render_html.py` here against the latest
`rows.json` and writes the result to `results/report.html`.

## Files

| File | Role |
|---|---|
| `render_html.py` | the renderer — invoked by `crucibleforge report`. Reads a `rows.json`-shaped dataset and emits a self-contained HTML page. |
| `script.js` | client-side behaviour: text filter, on-rig toggle, benched toggle, **the "Filter by element →" row with 9 dropdowns (RP / NSFW / Explicit peak / Willing / Steer / Code / Tools / Instruct / Reason, each with thresholds `any / ≥10 / ≥30 / ≥50 / ≥70 / ≥90 / 100`)**, sort, expand-row. |
| `styles.css` | the page chrome (dark / light, header, toolbar, table, badges, pills). |
| `example.html` | a copy of the last served filter-enabled render. Open this file in a browser to see the canonical board format without running a bench or hitting the network. |
| `example.rows.json` | the data the `example.html` was rendered against. Useful as a fixture when iterating on `render_html.py` or `script.js` — drop it next to `render_html.py` and re-run to reproduce the example exactly. |

A served copy of `example.html` may exist on the maintainer's tailnet (not part of this repo).

## When to read this

- An agent/thread needs to know what a CrucibleForge bench report looks like
  and `results/report.html` is not handy → **read `example.html` first**, it is
  the canonical example and what every fresh `crucibleforge report` render
  is compared against.
- Iterating on the template → edit `render_html.py` / `script.js` / `styles.css`,
  then re-render `example.html` from `example.rows.json` to verify the diff.

## Regenerating `example.html`

From `~/Projects/crucibleforge/`:

```bash
set -a; source ~/.openclaw/gateway.systemd.env; set +a
# 1. Re-render results/report.html from the latest data
uv run crucibleforge report
# 2. The filter-enabled shell (with component dropdowns) is built separately
#    from rows.json — sync it into the served dir and re-copy to this template.
#    Source-of-truth builder lives at /tmp/build_html.py.
python3 /tmp/build_html.py   # writes /tmp/cf-serve/index.html
cp /tmp/cf-serve/index.html  crucibleforge/templates/board/example.html
cp /tmp/cf-serve/rows.json   crucibleforge/templates/board/example.rows.json
```

(The `example.html` here is the filter-enabled shell, not the full
`results/report.html` — the shell loads `rows.json` at runtime, so the
template ships as one HTML file + one JSON fixture rather than a 150 kB
self-contained page.)
