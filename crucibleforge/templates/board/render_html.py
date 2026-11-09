"""CrucibleForge board: the ONE HTML renderer (``results/report.html``).

``crucibleforge report`` builds the rows from the same computed stats as
report.md (``report.html_rows``) — exact labels, numbers as numbers — and this
module bakes them into a self-contained page: inline CSS + JS, the rows as a
``<script type="application/json">`` blob, no CDN, no fetch, no sidecar file.

The design is the element-filter board the maintainer approved on 2026-09-20/22,
revised 2026-09-26 ("make it a table, expandable rows, sortable by element;
main = Chat, Coding, Overall, tok/s; customizable columns on the far right;
save my layout; filtering back") and again on 2026-11-03 ("for each header,
add a on click filter option, and move the filters in there"): fixed columns
``# · Model · Chat · Coding · Overall · tok/s``, then the viewer's chosen
columns (⚙ Columns: every component, Run date, Notes, Judge, Coverage,
Provider; default = the components in scoring order), click-to-sort on every
column, a text filter, a "benched only" toggle, and a **filter menu per
header** (the ▾ in a head: sort up/down, the operators for that column's
type, the minimum-score presets + a free number, a clear and the active
filter). A per-row ▸ expander (every component, Notes, Run date, judge). The
layout is saved per viewer — through DisPatch's tool-state bridge when
framed, else localStorage. At <= 720 px the phone gets Table | Cards (Table
by default); the cards have no headers, so every number on a card carries its
own ▾. See README.md.

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
#: suite 3.9: with the headline (rows carry ``s``) the cells show S ± m on the
#: rough scale (report.HEADLINE_FOOTNOTE) and "Overall" reads "Score"; sorting
#: and the element filters keep using the raw % (same order)
HEADLINE_LABELS = {"overall": "Score", "coding": "Code"}
#: choosable right-hand columns besides the components (key, header, kind)
INFO_COLUMNS = [("suite", "Suite", "text"), ("date", "Run date", "text"), ("notes", "Notes", "text"), ("judge", "Judge", "text"),
                ("coverage", "Coverage", "text"), ("provider", "Provider", "text"),
                ("room_cell", "Room", "text"),
                # what one run of the Code + Chat tests cost at the provider's price
                ("cost_usd", "Run cost", "num"),
                # suite 3.9: the Reason score and the tier reached per score
                ("reason", "Reason", "num"), ("tiers_cell", "Tiers", "text"),
                # suite 3.9 headline: the raw % behind S, and the paired verdict
                ("raw_cell", "Raw %", "text"), ("vs_above", "vs above", "text"),
                ("points_cell", "Points", "text")]


#: shown only when some row carries a ``base`` (models.yaml "Built on" tag), so a board without tags is unchanged
BASE_COLUMN = ("base", "Built on", "text")


def columns(comps: list[dict], headline: bool = False,
            scores: list[dict] | None = None, has_base: bool = False) -> tuple[list[dict], list[dict]]:
    """``(main, extra)`` column specs baked into the page. ``extra`` is what
    the ⚙ Columns chooser offers: every component (the default right-hand
    set, in scoring order), then the run-info columns. ``scores`` = a
    profile's declared scores ``[{key, label}]`` (key = the row's data key):
    the main columns are then # · Model · Score · <each score> · tok/s."""
    if scores:
        main = ([{"key": "rank", "label": "#", "kind": "num"},
                 {"key": "label", "label": "Model", "kind": "text"},
                 {"key": "overall", "label": "Score", "kind": "num"},
                 # 4.0: capability class (from Overall) and the Chat/Coding lean
                 {"key": "class", "label": "Class", "kind": "text"},
                 {"key": "lean", "label": "Lean", "kind": "text"}]
                + [{"key": x["key"], "label": x["label"], "kind": "num"} for x in scores]
                + [{"key": "tok_s", "label": "tok/s", "kind": "num"}])
    else:
        main = [{"key": k, "label": t, "kind": kind} for k, t, kind in MAIN_COLUMNS]
    if headline and not scores:
        main = [{**c, "label": HEADLINE_LABELS.get(c["key"], c["label"])} for c in main]
    extra = [{"key": f"c:{c['label']}", "label": c["label"], "kind": "num", "side": c["side"],
              "group": f"{c['side'].title()} component"} for c in comps]
    info = INFO_COLUMNS[:1] + ([BASE_COLUMN] if has_base else []) + INFO_COLUMNS[1:]
    extra += [{"key": k, "label": t, "kind": kind, "group": "Run info"} for k, t, kind in info]
    return main, extra


def _below(footer, legend, versus, ladder) -> str:
    """Everything under the table: short sections, then one closed
    "More detail" block holding the long footnotes."""
    esc = html.escape

    def section(cls, title, lines):
        lines = [x for x in (lines or []) if x]
        if not lines:
            return ""
        return (f'<section class="{cls}"><h2>{esc(title)}</h2><ul>'
                + "".join(f"<li>{esc(scrub(x))}</li>" for x in lines) + "</ul></section>")
    more = "".join(f"<p>{esc(scrub(line))}</p>" for line in (footer or []) if line)
    return ("<footer>" + section("versus", "Who beats whom", versus)
            + section("ladder", "Ladder packs", ladder)
            + section("legend", "How to read this", legend)
            + (f'<details class="more"><summary>More detail</summary>{more}</details>' if more else "")
            + "</footer>")


def render_html(rows: list[dict], subtitle: str = "",
                components: list[dict] | None = None,
                footer: list[str] | None = None,
                forbidden: list[str] | None = None,
                scores: list[dict] | None = None,
                sides: list[dict] | None = None,
                legend: list[str] | None = None,
                versus: list[str] | None = None,
                ladder: list[str] | None = None,
                part_labels: dict | None = None) -> str:
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

    The table head, the filter menus, the column launcher row and the column
    chooser are built by ``script.js`` from the viewer's saved layout; this
    page only carries the chrome and the data."""
    rows = scrub(rows)
    comps = scrub(_components(rows, components))
    if forbidden:
        bad = {t.lower() for t in forbidden}
        comps = [c for c in comps if c["label"].lower() not in bad]
        rows = [{**r, "components": {k: v for k, v in (r.get("components") or {}).items()
                                     if k.lower() not in bad}} for r in rows]
    has_base = any(r.get("base") for r in rows)
    main, extra = columns(comps, headline=any(r.get("s") for r in rows), scores=scores, has_base=has_base)
    sides = sides or [{"key": "chat", "label": "Chat"}, {"key": "coding", "label": "Coding"}]
    esc = html.escape
    # phone Sort select (the table headers sort too): every sortable key
    sort_opts = '<optgroup label="Headline">' + "".join(
        f'<option value="{c["key"]}">{esc("Rank" if c["key"] == "rank" else c["label"])}</option>'
        for c in main) + "</optgroup>"
    for side, title in ((x["key"], f"{x['label']} components") for x in sides):
        grp = "".join(f'<option value="c:{esc(c["label"])}">{esc(c["label"])}</option>'
                      for c in comps if c["side"] == side)
        if grp:
            sort_opts += f'<optgroup label="{title}">{grp}</optgroup>'
    sort_opts += '<optgroup label="Run info">' + "".join(
        f'<option value="{k}">{esc(t)}</option>'
        for k, t, _ in (INFO_COLUMNS[:1] + ([BASE_COLUMN] if has_base else []) + INFO_COLUMNS[1:])) + "</optgroup>"
    mobile = ('<div class="viewtog m-only" role="group" aria-label="phone layout">'
              '<button id="viewTable" type="button" aria-pressed="true">Table</button>'
              '<button id="viewCards" type="button" aria-pressed="false">Cards</button></div>'
              '<div class="m-sort m-only"><label class="m-lbl" for="sortSel">Sort</label>'
              f'<select id="sortSel" aria-label="sort by">{sort_opts}</select>'
              '<button id="sortDir" type="button" aria-label="sort direction">Top first</button></div>'
              '<button id="filtersBtn" type="button" aria-expanded="false" '
              'aria-controls="filtersRow">Filters</button>')
    latest = ('<label class="latest" title="Hide older versions of a model when a newer version is on the board. '
              'Fine-tunes are never hidden."><input type="checkbox" id="latestOnly"> latest versions only</label>'
              if any(r.get("superseded_by") for r in rows) else "")
    hide_inc = ('<label class="hide-inc" title="Hide models whose run is not finished yet (Incomplete tag)">'
                '<input type="checkbox" id="hideIncomplete"> hide incomplete</label>'
                if any(r.get("incomplete") for r in rows) else "")
    chooser = ('<section class="cols-panel" id="colsPanel" aria-label="choose columns">'
               '<div class="cols-head"><strong>Right-hand columns</strong>'
               '<span class="muted">tick to show · drag or ↑↓ to order · saved for you</span>'
               '<button id="colsDone" type="button">Done</button></div>'
               '<ol class="cols-list" id="colsList"></ol></section>')
    below = _below(footer, legend, versus, ladder)
    payload = data_json({"rows": rows, "components": comps, "thresholds": THRESHOLDS,
                         "main": main, "extra": extra, "sides": sides,
                         # "<score>/<part>" -> "Score / Part": names a row's maxed_parts
                         "part_labels": scrub(part_labels or {})})
    page = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="color-scheme" content="dark light">'
        '<title>CrucibleForge Board</title>'
        f"<style>{CSS}</style></head><body data-view=\"table\">"
        "<header><h1>CrucibleForge — Chat &amp; Coding</h1>"
        f'<div class="meta">{esc(scrub(subtitle))}</div>'
        '<div class="toolbar"><input id="filter" type="search" placeholder="search model / provider…" '
        'aria-label="search models">'
        f'{mobile}'
        '<label class="benched"><input type="checkbox" id="benched"> ranked only</label>'
        f'{latest}{hide_inc}'
        '<button id="colsBtn" type="button" aria-expanded="false" aria-controls="colsPanel">⚙ Columns</button>'
        '<button id="clearAll" type="button" title="Clear the search, the chips and the score filters">'
        'Clear filters</button>'
        '<button id="resetLayout" type="button" title="Back to the default columns, sort and filters">'
        'Reset layout</button></div>'
        '<div class="chips" id="chips" role="group" aria-label="quick filters"></div>'
        f'{chooser}'
        '<div class="filters-row" id="filtersRow"></div>'
        f'<div class="meta" id="count">{len(rows)} models</div>'
        "</header>"
        '<div class="fmenu" id="fmenu" role="menu" aria-label="column filter">'
        '<div class="fm-body" id="fmenuBody"></div>'
        '<div class="fm-active" id="fmenuActive"></div>'
        '</div>'
        '<div class="wrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>'
        '<div class="cards" id="cards"></div>'
        f"{below}"
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
