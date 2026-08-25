"""Tiny Markdown → HTML for the report (headings, tables, lists, emphasis,
inline code, blockquotes, paragraphs). Everything is HTML-escaped first, so
model output that leaks into notes cannot inject markup."""
from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*(?!\*)(.+?)(?<!\*)\*(?![*\w])")


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = _INLINE_CODE.sub(r"<code>\1</code>", s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITAL.sub(r"<em>\1</em>", s)
    return s


def _cell_class(text: str) -> str:
    t = text.strip()
    if t in ("✓",):
        return ' class="ok"'
    if t.startswith("⚠️") or t == "✗":
        return ' class="warn"'
    if t.endswith("%") or re.fullmatch(r"-?\d+(\.\d+)?", t):
        return ' class="num"'
    return ""


def _table(lines: list[str]) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in rows[1]):
        head, body = rows[0], rows[2:]
    else:
        head, body = None, rows
    out = ["<div class='tablewrap'><table>"]
    if head:
        out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>")
    out.append("<tbody>")
    for r in body:
        cells = []
        for i, c in enumerate(r):
            tag = "th" if i == 0 else "td"
            cells.append(f"<{tag}{_cell_class(c)}>{_inline(c)}</{tag}>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def md_to_html(md: str) -> str:
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            flush_para()
            i += 1
            continue
        if s.startswith("|"):
            flush_para()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para()
            lvl = len(m.group(1))
            text = m.group(2)
            anchor = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            out.append(f"<h{lvl} id='{anchor}'>{_inline(text)}</h{lvl}>")
            i += 1
            continue
        if s.startswith(">"):
            flush_para()
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(block)) + "</blockquote>")
            continue
        if re.match(r"^[-*]\s+", s):
            flush_para()
            out.append("<ul>")
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                out.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", lines[i].strip())) + "</li>")
                i += 1
            out.append("</ul>")
            continue
        if s.startswith("```"):
            flush_para()
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
            continue
        para.append(s)
        i += 1
    flush_para()
    return "\n".join(out)
