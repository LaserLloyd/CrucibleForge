"""4.5.1 token budgets (PLAN-451-LIMITS step 1): thinking and answer no longer share one cap.

The profile block ``budgets_451`` (profiles/bench45.yaml) turns them on; the 4.5.0 keys
(``thinking_max_tokens_*``, ``bank_budget``, the case files' ``budget``, the chain ``ceiling`` values) stay as they
were so they keep describing 4.5.0 rows.

- board regime: send ``min(cap, max(max_tokens x factor, floor)) + max_tokens`` -- a reasoning allowance followed
  by the category's own answer allowance (factor 10, floor 32,768, cap 57,344: instruct 33,536, rp 34,768,
  tooluse 33,792, coding/reasoning/math 63,488);
- ``case_budgets``: a fixed budget per case id pattern (CQ1-3 from-scratch programs: 131,072), applied by the runner
  only (the case dicts and their content hashes are untouched);
- ``ceilings``: a chain instance's ceiling, mapped by value (32,768 -> 49,152, 16,384 -> 32,768), again in the
  runner only (the chain block and its revision are untouched);
- ``thinker_context``: the window a registry thinker loads at, at least (81,920, so a 65,536 bank/CL budget is not
  clamped by prompt + slack).

Every new row carries ``budget_rev: 2`` and ``budget_reason``. A stored row cut below the budget its unit gets now
(finish ``length``, no ``budget_rev``) is owed again by a plain resume (:func:`superseded_by_budget`)."""
from __future__ import annotations

from fnmatch import fnmatchcase

#: profile key (top level; profiles.apply_profile copies it to cfg["defaults"])
KEY = "budgets_451"
#: stamped on every row made under the block
REV = 2


def block(cfg_or_defaults: dict | None) -> dict | None:
    """The ``budgets_451`` block of a config (or its ``defaults``), or None (4.5.0 budgets)."""
    d = cfg_or_defaults or {}
    b = (d.get("defaults") or {}).get(KEY) if "defaults" in d else d.get(KEY)
    return b if isinstance(b, dict) and b else None


def formula(b: dict, max_tokens: int) -> int:
    """The board-regime budget: reasoning allowance + answer allowance."""
    mt = int(max_tokens)
    allow = min(int(b.get("cap", 57344)), max(int(mt * float(b.get("factor", 10))), int(b.get("floor", 32768))))
    return int(allow + mt)


def case_budget(b: dict | None, case_id: str | None) -> int | None:
    """A ``case_budgets`` entry matching ``case_id`` (glob), or None."""
    if not b or not case_id:
        return None
    for pat, v in (b.get("case_budgets") or {}).items():
        if fnmatchcase(str(case_id), str(pat)):
            return int(v)
    return None


def ceiling(b: dict | None, value) -> int | None:
    """A chain ceiling after the ``ceilings`` map (by value); ``value`` unchanged when not mapped."""
    if value is None:
        return None
    m = (b or {}).get("ceilings") or {}
    return int(m.get(int(value), m.get(str(int(value)), value)))


def reason(kind: str, b: dict, detail: str = "") -> str:
    return f"4.5.1 budgets ({kind}{': ' + detail if detail else ''})"


def unit_budget(b: dict, row: dict, max_tokens_table: dict | None, bank_budget: int | None) -> int | None:
    """The budget the unit of a stored ``row`` is sent under ``b`` (None: not decidable from the row)."""
    if row.get("budget_rung"):
        return None                              # an escalation rung decides its own budget (never on the board)
    cb = case_budget(b, row.get("case_id"))
    if cb:
        return cb
    from .runner import BANK_CATEGORIES
    if row.get("category") in BANK_CATEGORIES:
        return int(bank_budget) if bank_budget else None
    ch = row.get("chain") or {}
    if ch:
        return ceiling(b, ch.get("ceiling")) if ch.get("ceiling") else None
    mt = (max_tokens_table or {}).get(row.get("category"))
    return formula(b, int(mt)) if mt else None


def superseded_by_budget(row: dict) -> bool:
    """A stored reply cut at a budget below the one its unit gets under the row's profile's ``budgets_451``: the
    request is the same but for a ceiling it reached, so the job is owed again (the first-reply rule otherwise keeps
    a cut reply for good)."""
    if row.get("budget_rev") or not (row.get("finish_reason") == "length" or row.get("truncated")):
        return False
    from .profiles import row_switch
    b = row_switch(row, KEY)
    if not isinstance(b, dict) or not b:
        return False
    want = unit_budget(b, row, row_switch(row, "max_tokens"), row_switch(row, "bank_budget"))
    sent = int(row.get("max_tokens_sent") or 0)
    return bool(want and sent and sent < int(want))
