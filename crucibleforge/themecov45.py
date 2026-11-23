"""Theme coverage map of the NSFW+ items against the requirement matrix (R17), 4.5 realignment W5.

Which (row, cell) of the matrix the 4.5 items ask for, read from the banks (each card's ``row`` field and its
mode), and the applicable cells nothing covers. Nothing here writes a card: uncovered cells are listed so an item
review can decide what would be needed.

Mode of each item (the matrix columns): taboo asks, unc asks and writing cards are single-shot writing at the row's
maximum rung (W-M); sessions and dark sessions are multi-turn at the maximum rung (RP-M); U3 questions are
knowledge (K-H, K-M) by topic. Dwell pieces carry no row. An item at a row's maximum rung is not counted for that
row's High cell unless ``max_covers_high`` is set (both are reported).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: the unc_know topics a level-6 hand may draw (law_history leaves the scored hands) -> matrix knowledge rows
TOPIC_ROW = {"health": "K1", "contra_sti": "K2", "kink": "K3", "law_history": "K4", "harm_reduction": "K5"}
SCORED_TOPICS = {"health", "contra_sti", "kink", "harm_reduction", "medical", "legal", "money"}
_ROW = re.compile(r"\bR\d{2}\b")


def bank_items() -> list[dict]:
    """[{id, row, cell, kind}] for every NSFW+ item that names a matrix row (read from the adult45 banks)."""
    from . import gen
    from .gen import adult45, unc_know
    gen.discover()
    out = []
    for variant, cell in (("taboo", "W-M"), ("uask", "W-M"), ("write", "W-M"), ("session", "RP-M"),
                          ("dark", "RP-M")):
        for it in adult45.items(variant):
            m = _ROW.search(str(it.get("row") or ""))
            if m:
                out.append({"id": it["id"], "row": m.group(0), "cell": cell, "kind": variant})
    topics = {q["topic"] for q in unc_know.load_bank({"type": "unc_know"})["items"]}
    for t in sorted(topics & SCORED_TOPICS):
        if t in TOPIC_ROW:
            for cell in ("K-H", "K-M"):
                out.append({"id": f"unc_know:{t}", "row": TOPIC_ROW[t], "cell": cell, "kind": "unc_know"})
    return out


def coverage(matrix: dict, items: list[dict] | None = None, max_covers_high: bool = False) -> dict:
    """{applicable, covered, uncovered: [[row, cell]], by_row: {row: {cell: [item ids]}}} against the matrix JSON."""
    items = bank_items() if items is None else items
    hit: dict[tuple[str, str], list[str]] = {}
    for it in items:
        cells = [it["cell"]]
        if max_covers_high and it["cell"].endswith("-M"):
            cells.append(it["cell"][:-1] + "H")
        for c in cells:
            hit.setdefault((it["row"], c), []).append(it["id"])
    applicable, covered, unc, by = 0, 0, [], {}
    for row in matrix["rows"]:
        if row.get("kind") == "preference":
            continue                                # preference rows are scored by the session checks, not by theme
        for col, cell in row["cells"].items():
            if cell is None or col.startswith("P-"):
                continue
            applicable += 1
            ids = hit.get((row["id"], col), [])
            by.setdefault(row["id"], {})[col] = ids
            if ids:
                covered += 1
            else:
                unc.append([row["id"], col])
    return {"applicable": applicable, "covered": covered, "uncovered": unc, "by_row": by,
            "uncovered_rows_in_every_cell": sorted(r for r, cs in by.items() if not any(cs.values()))}


def load_matrix(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
