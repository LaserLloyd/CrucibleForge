"""CrucibleForge board: the ONE HTML renderer (``results/report.html``).

``crucibleforge report`` builds the rows from the same computed stats as
report.md (``report.html_rows``) — exact labels, numbers as numbers — and this
module bakes them into a self-contained page: inline CSS + JS, the rows as a
``<script type="application/json">`` blob, no CDN, no fetch, no sidecar file.

The design is the element-filter board Jake approved on 2026-09-20/22,
revised 2026-09-26 ("make it a table, expandable rows, sortable by element;
main = Chat, Coding, Overall, tok/s; customizable columns on the far right;
save my layout; filtering back"): fixed columns ``# · Model · Chat · Coding ·
Overall · tok/s``, then the viewer's chosen columns (⚙ Columns: every
component, Run date, Notes, Judge, Coverage, Provider; default = the
components in scoring order), click-to-sort on every column, a text filter, a
"benched only" toggle, a "Filter by element →" row (≥ steps + a free number,
for Chat/Coding/Overall and every chosen numeric column), and a per-row ▸
expander (every component, Notes, Run date, judge). The layout is saved per
viewer — through DisPatch's tool-state bridge when framed, else localStorage.
At <= 720 px the phone gets Table | Cards (Table by default). See README.md.

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


#: fixed left-hand columns (always shown, in this order)
MAIN_COLUMNS = [("rank", "#", "num"), ("label", "Model", "text"), ("chat", "Chat", "num"),
                ("coding", "Coding", "num"), ("overall", "Overall", "num"), ("tok_s", "tok/s", "num")]
#: choosable right-hand columns besides the components (key, header, kind)
INFO_COLUMNS = [("date", "Run date", "text"), ("notes", "Notes", "text"), ("judge", "Judge", "text"),
                ("coverage", "Coverage", "text"), ("provider", "Provider", "text")]


def columns(comps: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(main, extra)`` column specs baked into the page. ``extra`` is what
    the ⚙ Columns chooser offers: every component (the default right-hand
    set, in scoring order), then the run-info columns."""
    main = [{"key": k, "label": t, "kind": kind} for k, t, kind in MAIN_COLUMNS]
    extra = [{"key": f"c:{c['label']}", "label": c["label"], "kind": "num", "side": c["side"],
              "group": f"{c['side'].title()} component"} for c in comps]
    extra += [{"key": k, "label": t, "kind": kind, "group": "Run info"} for k, t, kind in INFO_COLUMNS]
    return main, extra


def render_html(rows: list[dict], subtitle: str = "",
                components: list[dict] | None = None,
                footer: list[str] | None = None,
                forbidden: list[str] | None = None) -> str:
    """The full HTML document.

    ``rows``: ``report.html_rows`` shape. ``components``: ordered
    ``[{label, side: "chat"|"coding"}]`` (defaults to the labels found in the
    rows). ``footer``: plain-text lines (suite revision, judge, render time).
    ``forbidden``: for a PUBLIC page, the private terms (category names, case
    ids, labels — ``profiles.private_scope()["terms"]``). The caller has
    already left private rows out (report.py builds the public board from
    filtered rows); here a component column whose label is forbidden is
    dropped as well, and if any forbidden term still appears anywhere in the
    page, :class:`ValueError` is raised instead of returning it (fail closed).
    ``None`` = the operator's own board, rendered as is.

    The table head, the filter row and the column chooser are built by
    ``script.js`` from the viewer's saved layout; this page only carries the
    chrome and the data."""
    rows = scrub(rows)
    comps = scrub(_components(rows, components))
    if forbidden:
        bad = {t.lower() for t in forbidden}
        comps = [c for c in comps if c["label"].lower() not in bad]
        rows = [{**r, "components": {k: v for k, v in (r.get("components") or {}).items()
                                     if k.lower() not in bad}} for r in rows]
    main, extra = columns(comps)
    esc = html.escape
    # phone Sort select (the table headers sort too): every sortable key
    sort_opts = '<optgroup label="Headline">' + "".join(
        f'<option value="{c["key"]}">{esc("Rank" if c["key"] == "rank" else c["label"])}</option>'
        for c in main) + "</optgroup>"
    for side, title in (("chat", "Chat components"), ("coding", "Coding components")):
        grp = "".join(f'<option value="c:{esc(c["label"])}">{esc(c["label"])}</option>'
                      for c in comps if c["side"] == side)
        if grp:
            sort_opts += f'<optgroup label="{title}">{grp}</optgroup>'
    sort_opts += '<optgroup label="Run info">' + "".join(
        f'<option value="{k}">{esc(t)}</option>' for k, t, _ in INFO_COLUMNS) + "</optgroup>"
    mobile = ('<div class="viewtog m-only" role="group" aria-label="phone layout">'
              '<button id="viewTable" type="button" aria-pressed="true">Table</button>'
              '<button id="viewCards" type="button" aria-pressed="false">Cards</button></div>'
              '<div class="m-sort m-only"><label class="m-lbl" for="sortSel">Sort</label>'
              f'<select id="sortSel" aria-label="sort by">{sort_opts}</select>'
              '<button id="sortDir" type="button" aria-label="sort direction">Top first</button></div>'
              '<button id="filtersBtn" class="m-only" type="button" aria-expanded="false" '
              'aria-controls="filtersRow">Filters</button>')
    chooser = ('<section class="cols-panel" id="colsPanel" aria-label="choose columns">'
               '<div class="cols-head"><strong>Right-hand columns</strong>'
               '<span class="muted">tick to show · drag or ↑↓ to order · saved for you</span>'
               '<button id="colsDone" type="button">Done</button></div>'
               '<ol class="cols-list" id="colsList"></ol></section>')
    foot = "".join(f"<p>{esc(scrub(line))}</p>" for line in (footer or []) if line)
    payload = data_json({"rows": rows, "components": comps, "thresholds": THRESHOLDS,
                         "main": main, "extra": extra})
    page = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="color-scheme" content="dark light">'
        '<title>CrucibleForge Board</title>'
        f"<style>{CSS}</style></head><body data-view=\"table\">"
        "<header><h1>CrucibleForge — Chat &amp; Coding</h1>"
        f'<div class="meta">{esc(scrub(subtitle))}</div>'
        '<div class="toolbar"><input id="filter" type="search" placeholder="filter model / provider…" '
        'aria-label="filter models">'
        f'{mobile}'
        '<button id="expandAll" type="button">Expand all</button>'
        '<button id="collapseAll" type="button">Collapse all</button>'
        '<label class="benched"><input type="checkbox" id="benched"> benched only</label>'
        '<button id="colsBtn" type="button" aria-expanded="false" aria-controls="colsPanel">⚙ Columns</button>'
        '<button id="resetLayout" type="button" title="Back to the default columns, sort and filters">'
        'Reset layout</button></div>'
        f'{chooser}'
        '<div class="filters-row" id="filtersRow"></div>'
        f'<div class="meta" id="count">{len(rows)} models</div>'
        "</header>"
        '<div class="wrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>'
        '<div class="cards" id="cards"></div>'
        f"<footer>{foot}</footer>"
        f'<script type="application/json" id="board-data">{payload}</script>'
        f"<script>{JS}</script></body></html>"
    )
    if forbidden:
        low = page.lower()
        hits = [t for t in forbidden if t and t.lower() in low]
        if hits:
            raise ValueError(f"public board would contain private terms: {hits}")
    return page


def example(path: str | None = None) -> str:
    """Regenerate ``example.html`` from the REAL rows in results/ through the
    same code path as ``crucibleforge report --public`` (without writing any
    results file). example.html is TRACKED — it ships — so it is always the
    public board: private categories never reach it."""
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
    cfg = {**(cfg or {}), "_audience": report.PUBLIC}
    labels, stats, _ = report.board_stats(None, cfg)
    out = report.render_report_html(labels, stats, cfg)
    Path(path or Path(__file__).with_name("example.html")).write_text(out, encoding="utf-8")
    return out


__all__ = ["CSS", "INFO_COLUMNS", "JS", "MAIN_COLUMNS", "THRESHOLDS", "columns", "data_json", "example",
           "render_html", "scrub"]
