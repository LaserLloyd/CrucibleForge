"""CrucibleForge bench board HTML renderer (default report template).

The HTML board is the user-facing scorecard — mobile-first, dark-mode-aware,
sortable on every column, expandable per-row with click-to-sort-by-element.

Shipped as part of the package so `crucibleforge report` writes
``results/report.html`` alongside ``results/report.md`` by default.

The source lives in this package; the legacy wrapper at
``~/.openclaw/workspace/crucible-template/render_crucible_html.py``
is the build-time origin and is no longer required at runtime.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from importlib import resources

# Load styles.css + script.js from this package so they travel with the install.
with resources.files("crucibleforge.templates.board").joinpath("styles.css").open(
    "r", encoding="utf-8"
) as _f:
    CSS = _f.read()
with resources.files("crucibleforge.templates.board").joinpath("script.js").open(
    "r", encoding="utf-8"
) as _f:
    JS = _f.read()


def parse_report(text: str):
    """Pull the scorecard table out of a generated ``report.md``.

    Returns ``(header, rows)`` for the LARGEST table that has ``Model`` and
    ``Total`` columns; ``([], [])`` if none matches.
    """
    tables = []
    for m in re.finditer(r"(\|[^\n]+\|(?:\n\|[^\n]+\|)+)", text):
        lines = m.group(1).strip().split("\n")
        if len(lines) < 3:
            continue
        if not re.match(r"^\|[\s:\-|]+\|$", lines[1].strip()):
            continue
        hdr = [re.sub(r"\*+", "", c).strip() for c in lines[0].strip("|").split("|")]
        if "Model" not in hdr or "Total" not in hdr:
            continue
        rows = []
        for r in lines[2:]:
            cells = [re.sub(r"\*+", "", c).strip() for c in r.strip("|").split("|")]
            if len(cells) < len(hdr):
                cells += [""] * (len(hdr) - len(cells))
            if len(cells) > len(hdr):
                cells = cells[: len(hdr)]
            rows.append(cells)
        tables.append((hdr, rows))
    return max(tables, key=lambda t: len(t[1])) if tables else ([], [])


def reg_block(text: str, label: str):
    """Pull provider/ctx/model_id for one ``- name: <label>`` entry."""
    m = re.search(
        rf"^- name: {re.escape(label)}\n((?:  [^\n]+\n|\s*#[^\n]*\n)*)",
        text,
        re.MULTILINE,
    )
    if not m:
        return None
    b = m.group(0)
    prov = re.search(r"provider:\s*(\S+)", b)
    ctx = re.search(r"context_length:\s*(\d+)", b)
    mid = re.search(r"model_id:\s*(\S+)", b)
    return {
        "enabled": "enabled: false" not in b,
        "provider": prov.group(1) if prov else None,
        "ctx": int(ctx.group(1)) if ctx else None,
        "model_id": mid.group(1) if mid else None,
    }


def rig_mtime(label: str, cache_dir: str):
    """Best-effort: when did this model land on the rig?

    Returns ``YYYY-MM-DD`` or ``None``. The HF hub dir is the canonical cache
    on this box; a few legacy rigs used a separate ``rig-cache/`` sibling of
    the repo. We probe both and fall back silently.
    """
    candidates = []
    if cache_dir:
        candidates.append(cache_dir)
    candidates.append(os.path.expanduser("~/.cache/huggingface/hub"))
    for d in candidates:
        try:
            for f in os.listdir(d):
                if label.lower() in f.lower():
                    return datetime.fromtimestamp(
                        os.path.getmtime(os.path.join(d, f))
                    ).strftime("%Y-%m-%d")
        except OSError:
            continue
    return None


def parse_pct(s):
    if not s or s in ("\u2013", "-"):
        return None
    m = re.match(r"([\d.]+)", str(s))
    return float(m.group(1)) if m else None


def build_rows(report_text: str, registry_text: str, rig_cache: str):
    """Combine scorecard + registry into the JSON the HTML table needs."""
    hdr, sc_cells = parse_report(report_text)
    labels = re.findall(r"^- name: (\S+)", registry_text, re.MULTILINE)
    rows = []
    for label in labels:
        reg = reg_block(registry_text, label) or {
            "enabled": True,
            "provider": None,
            "ctx": None,
            "model_id": None,
        }
        sc = None
        for cells in sc_cells:
            model = re.sub(
                r"\s+v\S+\s+on\s+\d{4}-\d{2}-\d{2}.*$", "", cells[1]
            ).strip()
            if model == label:
                sc = cells
                break
        if not sc:
            for cells in sc_cells:
                model = re.sub(
                    r"\s+v\S+\s+on\s+\d{4}-\d{2}-\d{2}.*$", "", cells[1]
                ).strip()
                if label in model or model in label:
                    sc = cells
                    break
        if not sc:
            rows.append(
                {
                    "label": label,
                    "enabled": reg["enabled"],
                    "provider": reg["provider"] or "",
                    "ctx": reg["ctx"],
                    "date": rig_mtime(label, rig_cache),
                    "model_id": reg["model_id"],
                    "total": "\u2013",
                    "chat": "\u2013",
                    "code": "\u2013",
                    "judge": "\u2013",
                    "coverage": "\u2013",
                    "tok_s": "\u2013",
                    "comps": {},
                    "_comps": {},
                    "on_rig": reg["provider"] == "studioforge",
                    "_t": None,
                    "_c": None,
                    "_k": None,
                }
            )
            continue
        total = sc[7] if len(sc) > 7 else "\u2013"
        chat = sc[8] if len(sc) > 8 else "\u2013"
        code = sc[9] if len(sc) > 9 else "\u2013"
        coverage = sc[3] if len(sc) > 3 else "\u2013"
        tok_s = sc[5] if len(sc) > 5 else "\u2013"
        judge = sc[4] if len(sc) > 4 else "\u2013"
        comps = {
            "rp": sc[10] if len(sc) > 10 else "\u2013",
            "nsfw": sc[11] if len(sc) > 11 else "\u2013",
            "explicit_peak": sc[12] if len(sc) > 12 else "\u2013",
            "willing": sc[13] if len(sc) > 13 else "\u2013",
            "steer": sc[14] if len(sc) > 14 else "\u2013",
            "code_raw": sc[15] if len(sc) > 15 else "\u2013",
            "tools": sc[16] if len(sc) > 16 else "\u2013",
            "instruct": sc[17] if len(sc) > 17 else "\u2013",
            "reason": sc[18] if len(sc) > 18 else "\u2013",
        }
        _comps = {k: parse_pct(v) for k, v in comps.items()}
        rows.append(
            {
                "label": label,
                "enabled": reg["enabled"],
                "provider": reg["provider"] or "",
                "ctx": reg["ctx"],
                "date": rig_mtime(label, rig_cache),
                "model_id": reg["model_id"],
                "total": total,
                "chat": chat,
                "code": code,
                "coverage": coverage,
                "tok_s": tok_s,
                "judge": judge,
                "comps": comps,
                "_comps": _comps,
                "on_rig": reg["provider"] == "studioforge",
                "_t": parse_pct(total),
                "_c": parse_pct(chat),
                "_k": parse_pct(code),
            }
        )
    return rows


def render_html(rows) -> str:
    """Return the full HTML document with rows baked into the JS payload."""
    rows_json = json.dumps(rows)
    js_final = JS.replace("ROWS_PLACEHOLDER", rows_json)
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" '
        f'content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<title>CrucibleForge \u2014 Bench Board</title>'
        f"<style>{CSS}</style></head><body>"
        f"<header><h1>CrucibleForge</h1>"
        f'<div class="toolbar">'
        f'<input id="filter" placeholder="filter label / provider..." '
        f'style="flex:1;min-width:160px;">'
        f'<button id="expandAll">Expand all</button>'
        f'<button id="collapseAll">Collapse all</button>'
        f'<label><input type="checkbox" id="onRig"> on-rig only</label>'
        f'<label><input type="checkbox" id="benched"> benched only</label>'
        f"</div></header>"
        f"<main>"
        f'<div class="meta">{len(rows)} models \u00b7 '
        f'{sum(1 for r in rows if r["_t"] is not None)} benched \u00b7 '
        f'{sum(1 for r in rows if r["on_rig"])} on rig \u00b7 '
        f"click any column to sort \u00b7 click \u25b8 to expand</div>"
        f"<table><thead><tr>"
        f'<th style="width:24px"></th>'
        f'<th data-k="label">Label <span class="arrow"></span></th>'
        f'<th data-k="provider">Provider <span class="arrow"></span></th>'
        f'<th data-k="date">Date dl <span class="arrow"></span></th>'
        f'<th data-k="ctx">Ctx <span class="arrow"></span></th>'
        f'<th data-k="total">Total <span class="arrow"></span></th>'
        f'<th data-k="chat">Chat <span class="arrow"></span></th>'
        f'<th data-k="code">Code <span class="arrow"></span></th>'
        f'<th data-k="coverage">Coverage <span class="arrow"></span></th>'
        f'<th data-k="tok_s">tok/s <span class="arrow"></span></th>'
        f'<th data-k="judge">Judge <span class="arrow"></span></th>'
        f"</tr></thead><tbody id=\"tbody\"></tbody></table>"
        f"</main>"
        f"<script>{js_final}</script></body></html>"
    )


__all__ = [
    "CSS",
    "JS",
    "parse_report",
    "reg_block",
    "rig_mtime",
    "parse_pct",
    "build_rows",
    "render_html",
]