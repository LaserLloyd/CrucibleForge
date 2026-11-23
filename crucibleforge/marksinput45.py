"""What the mark checks read from a finished run: the segment values BEFORE any mark holds them back, and the
per-reply facts the act-and-beat marks need. Report time only: stores nothing, scores nothing, changes no number.

The report keeps, per model, ``marks_input45`` = ``{segments, ab_units, know, error}``. A held segment (marks not yet
applied) shows no number on the board, so a script that applies the marks cannot read the board's cell; it reads this
block instead (``scripts/check_marks45.py``)."""
from __future__ import annotations

import copy
from typing import Any


#: NSFW+ number -> the prefix of its segment keys (``nsfw/erotica``, ``unc/willingness``); each number also has a
#: ``headline/<number>`` key. These are the adult page's keys: that page runs once per model, so a model's other runs
#: carry none of them (scripts/check_marks45.py may skip such a run for these keys only).
NSFW_PREFIX = {"nsfw": "nsfw", "uncensored": "unc"}


def adult_key(key: str) -> bool:
    """``key`` is a segment key of the adult (NSFW+) page."""
    return (any(key.startswith(p + "/") for p in NSFW_PREFIX.values())
            or key in {f"headline/{nk}" for nk in NSFW_PREFIX})


def segments(raw_tb: dict | None, nsfw_numbers: dict | None) -> dict:
    """{segment key: {points, partial, max, margin, complete, reason}}. ``points`` is None while a part is pending
    (``partial`` is then what the finished parts add up to); keys are the mark file's (``code/writes`` ...,
    ``nsfw/erotica`` ..., ``unc/willingness`` ..., ``headline/nsfw``, ``headline/uncensored``)."""
    out: dict = {}
    for sk in ("code", "chat"):
        sect = (raw_tb or {}).get(sk) or {}
        for k, g in (sect.get("segments") or {}).items():
            why = sorted({p["reason"] for p in g.get("pending") or []})
            out[f"{sk}/{k}"] = {"points": g["points"] if g.get("complete") else None, "partial": g.get("points"),
                                "max": g["max"], "margin": g.get("margin"), "complete": bool(g.get("complete")),
                                "reason": "; ".join(why) or None}
    prefix = NSFW_PREFIX
    for nk, num in (nsfw_numbers or {}).items():
        for sk, g in (num.get("segments") or {}).items():
            out[f"{prefix[nk]}/{sk}"] = {"points": g.get("value"), "partial": g.get("value"), "max": g["max"],
                                         "margin": g.get("margin"), "complete": g.get("value") is not None,
                                         "reason": g.get("pending"),
                                         # the parts as scored (the sanity block reads them: plan B4)
                                         "parts": dict(g.get("parts") or {})}
        out[f"headline/{nk}"] = {"points": num.get("value"), "partial": num.get("partial"),
                                 "max": float(num.get("of") or 10), "margin": num.get("margin"),
                                 "complete": num.get("value") is not None, "reason": num.get("pending")}
    return out


def ab_units(rows: list[dict], label: str, results, profile: dict | None) -> list[dict]:
    """One record per classed sexual NSFW+ unit that ran to the end: the readers' majority class, whether the act
    key exists and finds the asked acts in the scored region, and the share of brief items (written / dwell cards)."""
    from . import actkeys45, lineage, nsfwplus45 as np
    units = np.units_from_rows(rows)
    classed = [u for u in units if u.get("classing")]
    if not classed:
        return []
    from pathlib import Path
    verdicts, _why = np.offline_verdicts(units, Path(results) / "readers45" / f"{label}.jsonl",
                                         lineage.family(label), profile, results=results)
    out = []
    for u in np.apply_cut_rule(classed, profile):
        st = u.get("status") or {}
        if not u["classing"].get("sexual") or any(st.get(k) for k in ("cut", "filtered", "empty", "dnf")):
            continue
        if (u.get("code") or {}).get("refused"):
            continue
        v = verdicts.get(u["id"])
        cls = (v or {}).get("cls") if isinstance(v, dict) else None
        keyed = bool(actkeys45.acts_for(u["card"]))
        present = bool(actkeys45.acts_present(u["card"], np.region(u))) if keyed else None
        brief = None
        if u["segment"] in ("erotica_written", "erotica_dwell"):
            try:
                brief = float(np.brief_share(u["card"], list(u["replies"])))
            except Exception:                      # noqa: BLE001 -- a card without brief items: not a writing card
                brief = None
        out.append({"id": u["id"], "card": u["card"], "segment": u["segment"], "cls": cls, "act_key": keyed,
                    "act_present": present, "brief": brief})
    return out


def know(rows: list[dict]) -> dict | None:
    """KNOWGAP pairs of one run: for every contrast pair whether its answerable (value) side was answered
    ('value qN ok' in the pair's detail). None when the run holds no KNOWGAP row."""
    sides: list[bool] = []
    n = 0
    for r in rows or []:
        ch = r.get("chain") or {}
        if ch.get("family") != "knowgap" or "h6_know" not in str(ch.get("part") or ""):
            continue
        n += 1
        for e in ((r.get("elements") or {}).get("results") or []):
            sides.append(str(e.get("detail") or "").startswith("value q") and " ok;" in str(e.get("detail")))
    return {"rows": n, "pairs": len(sides), "value_ok": sum(sides)} if n else None


def build(rows: list[dict] | None, label: str, results, profile: dict | None, raw_tb: dict | None,
          nsfw_numbers: dict | None) -> dict:
    out: dict[str, Any] = {"segments": segments(raw_tb, copy.deepcopy(nsfw_numbers)), "ab_units": [], "know": None}
    if rows is not None:
        try:
            out["ab_units"] = ab_units(rows, label, results, profile)
        except Exception as e:                     # noqa: BLE001 -- the marks then read the units as absent
            out["error"] = f"ab units: {type(e).__name__}: {e}"
        out["know"] = know(rows)
    return out
