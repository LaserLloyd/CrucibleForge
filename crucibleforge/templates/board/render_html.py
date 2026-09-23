"""CrucibleForge board: the ONE HTML renderer (``results/report.html``).

``crucibleforge report`` builds the rows from the same computed stats as
report.md (``report.html_rows``) — exact labels, numbers as numbers — and this
module bakes them into a self-contained page (no CDN, no network): a sortable
scorecard (# · Model · Chat · Coding · Overall · tok/s · Run date · Notes),
a text filter, and a per-row expander with the component breakdown.
"""
from __future__ import annotations

import html
import json
from importlib import resources

_PKG = "crucibleforge.templates.board"
CSS = resources.files(_PKG).joinpath("styles.css").read_text(encoding="utf-8")
JS = resources.files(_PKG).joinpath("script.js").read_text(encoding="utf-8")

#: the single substitution point in script.js
ROWS_TOKEN = "/*ROWS*/[]"


def rows_json(rows: list[dict]) -> str:
    """JSON safe to embed in a <script> element: "</" can never close the
    element early, and U+2028/2029 cannot break the JS string grammar."""
    return (json.dumps(rows, ensure_ascii=False, allow_nan=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_html(rows: list[dict], subtitle: str = "") -> str:
    """The full HTML document with ``rows`` baked into the script."""
    if ROWS_TOKEN not in JS:
        raise RuntimeError("script.js lost its /*ROWS*/[] token")
    js = JS.replace(ROWS_TOKEN, rows_json(rows))
    cols = [("rank", "#"), ("label", "Model"), ("chat", "Chat"), ("coding", "Coding"),
            ("overall", "Overall"), ("tok_s", "tok/s"), ("date", "Run date"),
            ("notes", "Notes")]
    head = "".join(f'<th data-k="{k}" scope="col">{html.escape(t)} <span class="arrow"></span></th>'
                   for k, t in cols)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<title>CrucibleForge Board</title>'
        f"<style>{CSS}</style></head><body>"
        "<header><h1>CrucibleForge — Chat &amp; Coding</h1>"
        f'<div class="meta">{html.escape(subtitle)}</div>'
        '<div class="toolbar"><input id="filter" placeholder="filter model…" aria-label="filter">'
        '<button id="expandAll">Expand all</button><button id="collapseAll">Collapse all</button>'
        "</div></header><main>"
        f'<div class="meta" id="count">{len(rows)} models · click a column to sort · '
        "click ▸ for components</div>"
        f'<table><thead><tr><th style="width:24px"></th>{head}</tr></thead>'
        '<tbody id="tbody"></tbody></table>'
        f"</main><script>{js}</script></body></html>"
    )


__all__ = ["CSS", "JS", "ROWS_TOKEN", "example", "render_html", "rows_json"]


def example(path: str | None = None) -> str:
    """Write example.html from made-up rows (no real results needed)."""
    from pathlib import Path
    rows = []
    for i, (label, chat, coding, tps, notes, tier) in enumerate([
            ("example-27b-a", 86.5, 88.9, 34.2, "", 0),
            ("example-27b-b", 88.1, 77.8, 31.0, "", 0),
            ("example-api", 83.5, 80.6, 140.0, "", 0),
            ("example-8b", 72.0, 25.0, 120.5, "partial (30/33 cases)", 1),
            ("example-bad-id", None, None, None, "FAILED: example/X-NVFP4 is not served…", 2)], 1):
        comps = {} if chat is None else {
            "RP": 88, "NSFW": 74, "Story": 68, "Explicit peak": 100, "Willing": 100, "Steer": 100,
            "Programs": coding, "Tools": 100, "Instruct": 75, "Reason": 50}
        overall = None if chat is None else round((chat * 55 + coding * 45) / 100, 1)
        rows.append({"rank": i, "label": label, "model_id": f"publisher/{label}-GGUF/{label}-Q5_K_M",
                     "provider": "studioforge", "chat": chat, "coding": coding, "overall": overall,
                     "tok_s": tps, "date": "2026-09-23", "notes": notes, "tier": tier,
                     "components": comps})
    out = render_html(rows, "example board — made-up numbers")
    Path(path or Path(__file__).with_name("example.html")).write_text(out, encoding="utf-8")
    return out
