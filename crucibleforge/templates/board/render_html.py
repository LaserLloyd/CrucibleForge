"""CrucibleForge board: the ONE HTML renderer (``results/report.html``).

``crucibleforge report`` builds the rows from the same computed stats as
report.md (``report.html_rows``) — exact labels, numbers as numbers — and this
module bakes them into a self-contained page: inline CSS + JS, the rows as a
``<script type="application/json">`` blob, no CDN, no fetch, no sidecar file.

The design is the element-filter board Jake approved on 2026-09-20/22
("the default template for these reports"): sticky header, centred numbers,
click-to-sort on EVERY column including each component, a text filter, a
"benched only" toggle, a "Filter by element →" row of min-score dropdowns
(one per component), and a per-row ▸ expander with the components, Notes and
the judge line. At <= 720 px (a phone in DisPatch's frame) the same rows render
as one card per model instead of the wide table — see README.md. Columns: ``# · Model · Chat · Coding · Overall · tok/s ·
Run date · Notes`` then one column per component on the far right.

The page is served by DisPatch in a sandboxed frame, so it must carry no
IPs, URLs or hostnames: every string is passed through :func:`scrub`.
"""
from __future__ import annotations

import html
import json
import re
from importlib import resources

_PKG = "crucibleforge.templates.board"
CSS = resources.files(_PKG).joinpath("styles.css").read_text(encoding="utf-8")
JS = resources.files(_PKG).joinpath("script.js").read_text(encoding="utf-8")

#: min-score choices in every "Filter by element →" dropdown
THRESHOLDS = [10, 30, 50, 70, 90, 100]

_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>)]+")
# a dotted quad right after "version " / "v" is a version string (a vendor
# build like 1.26.8.28), not an address ("v1.2.3.4" is already excluded by \w)
_IPV4 = re.compile(r"(?<![\w.])(?<![Vv]ersion )\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?(?![\w.])")
_HOST = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:ts\.net|local|lan|internal|home\.arpa)\b"
                   r"(?::\d+)?|\blocalhost:\d+\b", re.IGNORECASE)


def scrub(value):
    """Strip URLs, IPv4 addresses and private host names from every string
    in ``value`` (recursively) — error text in Notes can quote an endpoint."""
    if isinstance(value, str):
        return _HOST.sub("[host]", _IPV4.sub("[host]", _URL.sub("[url]", value)))
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def data_json(payload: dict) -> str:
    """JSON safe inside ``<script type="application/json">``: ``<``, ``>`` and
    ``&`` are \\u-escaped, so no model name or error text can close the
    element or open a comment; U+2028/2029 are escaped too."""
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _components(rows: list[dict], components: list[dict] | None) -> list[dict]:
    if components:
        return [{"label": c["label"], "side": c.get("side") or "chat"} for c in components]
    seen: dict[str, None] = {}
    for r in rows:
        for k in (r.get("components") or {}):
            seen.setdefault(k, None)
    return [{"label": k, "side": "chat"} for k in seen]


def render_html(rows: list[dict], subtitle: str = "",
                components: list[dict] | None = None,
                footer: list[str] | None = None) -> str:
    """The full HTML document.

    ``rows``: ``report.html_rows`` shape. ``components``: ordered
    ``[{label, side: "chat"|"coding"}]`` (defaults to the labels found in the
    rows). ``footer``: plain-text lines (suite revision, judge, render time)."""
    rows = scrub(rows)
    comps = _components(rows, components)
    esc = html.escape
    base = [("rank", "#"), ("label", "Model"), ("chat", "Chat"), ("coding", "Coding"),
            ("overall", "Overall"), ("tok_s", "tok/s"), ("date", "Run date"), ("notes", "Notes")]
    head = "".join(f'<th data-k="{k}" scope="col" aria-sort="none">{esc(t)} '
                   f'<span class="arrow"></span></th>' for k, t in base)
    head += "".join(f'<th data-k="c:{esc(c["label"])}" scope="col" aria-sort="none" '
                    f'class="side-{esc(c["side"])}" title="{esc(c["side"].title())} component">'
                    f'{esc(c["label"])} <span class="arrow"></span></th>' for c in comps)
    opts = '<option value="">any</option>' + "".join(
        f'<option value="{t}">{"≥" if t < 100 else ""}{t}</option>' for t in THRESHOLDS)
    filters = "".join(
        f'<label class="comp-filter side-{esc(c["side"])}" data-c="{esc(c["label"])}">'
        f'<span class="lbl">{esc(c["label"])}</span>'
        f'<select aria-label="minimum {esc(c["label"])}">{opts}</select></label>' for c in comps)
    # phone controls (<= 720 px): one "Sort" select + direction toggle, and the
    # element filters behind one "Filters (n)" disclosure. Same JS state as the
    # table headers, so rotating the phone keeps the view.
    sort_opts = '<optgroup label="Headline">' + "".join(
        f'<option value="{k}">{esc(t if k != "rank" else "Rank")}</option>'
        for k, t in base if k != "notes") + "</optgroup>"
    for side, title in (("chat", "Chat components"), ("coding", "Coding components")):
        grp = "".join(f'<option value="c:{esc(c["label"])}">{esc(c["label"])}</option>'
                      for c in comps if c["side"] == side)
        if grp:
            sort_opts += f'<optgroup label="{title}">{grp}</optgroup>'
    mobile = ('<div class="m-sort m-only"><label class="m-lbl" for="sortSel">Sort</label>'
              f'<select id="sortSel" aria-label="sort by">{sort_opts}</select>'
              '<button id="sortDir" type="button" aria-label="sort direction">Top first</button></div>'
              '<button id="filtersBtn" class="m-only" type="button" aria-expanded="false" '
              'aria-controls="filtersRow">Filters</button>')
    foot = "".join(f"<p>{esc(scrub(line))}</p>" for line in (footer or []) if line)
    payload = data_json({"rows": rows, "components": comps, "thresholds": THRESHOLDS})
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="color-scheme" content="dark light">'
        '<title>CrucibleForge Board</title>'
        f"<style>{CSS}</style></head><body>"
        "<header><h1>CrucibleForge — Chat &amp; Coding</h1>"
        f'<div class="meta">{esc(scrub(subtitle))}</div>'
        '<div class="toolbar"><input id="filter" type="search" placeholder="filter model / provider…" '
        'aria-label="filter models">'
        f'{mobile}'
        '<button id="expandAll">Expand all</button><button id="collapseAll">Collapse all</button>'
        '<label><input type="checkbox" id="benched"> benched only</label></div>'
        f'<div class="filters-row" id="filtersRow"><span class="flabel">Filter by element →</span>{filters}'
        '<button id="clearFilters" title="Clear all element filters">Clear</button></div>'
        f'<div class="meta" id="count">{len(rows)} models</div>'
        "</header>"
        f'<div class="wrap"><table><thead><tr><th scope="col" style="width:24px"></th>{head}</tr></thead>'
        '<tbody id="tbody"></tbody></table></div>'
        '<div class="cards" id="cards"></div>'
        f"<footer>{foot}</footer>"
        f'<script type="application/json" id="board-data">{payload}</script>'
        f"<script>{JS}</script></body></html>"
    )


def example(path: str | None = None) -> str:
    """Regenerate ``example.html`` from the REAL rows in results/ through the
    same code path as ``crucibleforge report`` (without writing any results
    file)."""
    from pathlib import Path

    from ... import report
    from ...config import load_config, results_dir, set_results_dir
    here = results_dir()
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001 — a missing registry still renders the board
        cfg = None
    finally:
        set_results_dir(here)
    labels, stats, _ = report.board_stats(None, cfg)
    out = report.render_report_html(labels, stats, cfg)
    Path(path or Path(__file__).with_name("example.html")).write_text(out, encoding="utf-8")
    return out


__all__ = ["CSS", "JS", "THRESHOLDS", "data_json", "example", "render_html", "scrub"]
