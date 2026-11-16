"""Early stop (R3, minimal, MakoBench M1): stop a model's run when its first
answers show it is not producing answers.

After the first ``after_answers`` answers of a model's run (default 20) and on
every answer up to ``max_answers`` (default 30), the run is judged on the
answers so far, in the order they were persisted (= COMPLETION order).

Ordering assumption (recorded on purpose, owner ruling 2026-11-13: thinking ON
for Main): the window is NOT "the easy items first". 4.5 Main has no ladder
(bench45 ``ladder: block``) and ``runner.schedule_jobs`` starts the longest jobs
first (code bank, sessions, then LPT by measured time); the first 20-30
COMPLETED answers are therefore a mix, skewed toward the shorter jobs that
finish while the long ones still run. A model that legitimately spends its
budget thinking on hard items is protected by the token-limit rule below (only
a cut-off or missing ANSWER counts, never a long think that still answered) and
by the threshold's calibration on stored full runs (lab EXP-20261113-14); a
hard item that completes inside the window and is cut off does count. Packs
that change the job mix (rc1) must be re-calibrated with the replay tool.

Chains (4.5 Main: short chains, one instance per rung, a chain stops at its
first failure): the counter is per model RUN, not per chain. It counts every
answer row the run persists, in completion order, whichever chain and rung it
belongs to; interleaved chains, chains of different lengths and chains that
stop early all feed the same window. The early window therefore sits mostly on
the first (easy) rungs of the chains. A chain engine must run its steps inside
the run-start rounds of ``runner._run_one_model`` (or hand the same
``carry["early_stop"]`` counter to whatever rounds it uses): recover-style
``only_jobs`` rounds and escalation rungs are deliberately not judged.

Only a model's run START is judged: a targeted rerun (a case subset of the
profile, e.g. ``--cases`` or a bench-all fill-in group), a recover round and an
escalation rung are not (their jobs are chosen because they are hard; the two
stored false positives at 0.25 were exactly such reruns).

Signals:

* ``blank``       — no effective answer: :func:`effective_answer` is empty and the
  row made no tool call. An answer a FINISHED reply left in the reasoning
  channel counts as an answer (the post-30cd052 rule), a cut-off one never;
  under the 4.5 ``empty_stop_rule`` a row stamped ``empty_stop`` counts as blank.
* ``token_limit`` — a run-health signal for looping, never a scoring budget
  (owner ruling R23: no per-instance token budget in Main; models think as
  designed). It counts a reply the SERVER cut at the hard limit in force for
  that request (``finish_reason == "length"``: the ``max_tokens`` actually sent
  — recorded per row as ``max_tokens_sent`` — or the context end), whatever
  that number was; no fixed token count is compared anywhere. A model that
  thinks for a very long time and then finishes (finish=stop) is not counted,
  nor is a wall-clock / turn cut (``wall_timeout``: a different limit).
* ``misrouted``   — a finished reply (finish=stop) with empty content, no tool
  call and a non-empty reasoning channel: the answer landed in the thinking
  channel (a template trap). It scores since 30cd052 but still trips at
  ``misrouted_rate_gt`` (0.25; None = report only): the run is measuring a
  template, not the model (replay v2: Nanbeige 0.55 at answer 20, every good
  stored run start <= 0.13).

* ``degenerate``  — a reply dominated by one repeated character or one short n-gram
  (:mod:`degenerate`: the last 4,096 characters compress below 0.10). Judged from
  answer ``degenerate_after_answers`` (default 4, earlier than the other signals: a
  degenerate instance must not spend 20 answers' time) up to ``max_answers``; trips
  above ``degenerate_rate_gt`` (0.25; None = report only). Stored degenerate 8B runs
  (2026-11-14) trip at answer 4-5; no stored sane run has a degenerate row in its start.
  The run-start probe (``degenerate.probe_failure``) normally stops such a model first.

It trips when ``blank`` > ``blank_rate_gt`` or ``token_limit`` >
``token_limit_rate_gt`` (fractions of the answers judged). The thresholds come
from the profile's ``early_stop:`` block (``profiles/bench45.yaml``); keys it
omits fall back to :data:`DEFAULTS` and the record says which source was used.

An "answer" is a persisted transcript row that is not ``skipped``, not an
``error`` row, and not a ``reasoning_answer_repair`` copy; a multi-turn session
contributes one answer per turn row.

Fail closed: a row that cannot be classified makes the check unusable; the
record carries ``error`` and the meta is stamped not publishable — never a
silent pass. On a trip the runner raises ``runner.EarlyStopError`` (a
``ModelRunError``): it drains this model, keeps its rows, writes the record to
the meta (``early_stop``, ``publishable: false``) and the batch goes on with the
next model.
"""
from __future__ import annotations

import threading

from . import degenerate as _degenerate


def effective_answer(row: dict) -> str:
    """A stored row's effective answer: ``api.row_answer`` (dev ``30cd052``, merged): content wins
    whenever there is any; else a reply that FINISHED (finish=stop) with no tool call and a non-empty
    reasoning channel is read from the reasoning channel; a cut-off, dropped or filtered reply never is."""
    from .api import row_answer
    return row_answer(row)


def misrouted(row: dict) -> bool:
    """A FINISHED reply whose whole answer is in the reasoning channel: content
    empty, no tool call, non-empty reasoning, finish=stop. (A cut-off think is
    the token-limit signal, not this one.)"""
    return (row.get("finish_reason") == "stop" and not (row.get("response") or "").strip()
            and not row.get("tool_calls") and bool((row.get("reasoning") or "").strip()))


def token_limited(row: dict) -> bool:
    """The server cut this reply at the hard output/context limit in force for
    the request (finish=length), or the runner could not send a session's turn
    because its context was exhausted (finish=context, chains context accounting):
    counted like "ran out". Keyed on the finish reason, not on a number."""
    return row.get("finish_reason") in ("length", "context")

DEFAULTS = {"enabled": True, "after_answers": 20, "max_answers": 30,
            "blank_rate_gt": 0.50, "token_limit_rate_gt": 0.20,
            "misrouted_rate_gt": 0.25,
            "degenerate_after_answers": 4, "degenerate_rate_gt": 0.25}

#: signal names, in the order they are checked (the first that trips is the
#: one recorded as ``signal``; every tripped one is in ``signals``)
TRIP_SIGNALS = (("blank", "blank_rate_gt"), ("token_limit", "token_limit_rate_gt"),
                ("misrouted", "misrouted_rate_gt"), ("degenerate", "degenerate_rate_gt"))


def describe(record: dict) -> str:
    """One line for logs and the meta ``error``."""
    if record.get("reason"):
        return f"early stop: {record['reason']}"
    return (f"early stop: {record['signal']} rate {record['value']:.2f} > "
            f"{record['threshold']} after {record['after_answers']} answers")


def settings(cfg: dict | None) -> dict:
    """The effective thresholds: the profile block (cfg.defaults.early_stop,
    copied there by profiles.apply_profile) over :data:`DEFAULTS`."""
    block = ((cfg or {}).get("defaults") or {}).get("early_stop")
    out = dict(DEFAULTS)
    src = "default"
    if isinstance(block, dict):
        out.update({k: block[k] for k in DEFAULTS if k in block})
        # the degenerate keys (2026-11-14) default quietly; the record lists them anyway
        core = ("after_answers", "max_answers", "blank_rate_gt", "token_limit_rate_gt", "misrouted_rate_gt")
        src = "profile" if all(k in block for k in core) else "profile+default"
    out["source"] = src
    return out


def is_answer(row: dict) -> bool:
    return not (row.get("skipped") or row.get("error") or row.get("grade") == "error"
                or row.get("reasoning_answer_repair"))


def classify(row: dict) -> dict:
    """The three signals of one answer row. Raises on a row it cannot read
    (the caller turns that into a fail-closed error)."""
    if not isinstance(row, dict):
        raise TypeError(f"row is {type(row).__name__}, not a dict")
    tools = row.get("tool_calls")
    return {
        # an unsent turn (finish=context) is the token-limit signal, not a blank answer
        # a row stamped empty_stop (4.5 empty_stop_rule) is a blank: a finished reply with
        # no visible content is not read from its reasoning here
        "blank": ((not tools) and not effective_answer(row).strip()
                  and row.get("finish_reason") != "context") or bool(row.get("empty_stop")),
        "misrouted": misrouted(row),
        "token_limit": token_limited(row),
        "degenerate": bool(_degenerate.row_degenerate(row)),
    }


class EarlyStop:
    """Per-model-run counter. :meth:`observe` is called with each batch of
    persisted rows (the runner holds its lock); it returns the trip record or
    None. Thread-safe on its own as well."""

    def __init__(self, cfg: dict | None = None, *, label: str | None = None,
                 skip: str | None = None):
        self.s = settings(cfg)
        self.label = label
        #: why this run is not judged (a targeted rerun); recorded, never a trip
        self.skip = skip
        self.n = 0
        self.counts = {"blank": 0, "misrouted": 0, "token_limit": 0, "degenerate": 0}
        #: the hard limits (max_tokens_sent) in force on the rows cut at the limit
        self.limits: set[int] = set()
        self.error: str | None = None
        self.tripped: dict | None = None
        self.last_eval: dict | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.s.get("enabled", True)) and not self.skip

    def _rates(self) -> dict:
        n = self.n
        return {k: (v / n if n else None) for k, v in self.counts.items()}

    def observe(self, rows: list[dict]) -> dict | None:
        """Count ``rows`` (in persist order) and judge after each answer inside
        the window. Returns the trip record (also kept on ``self.tripped``)."""
        if not self.enabled:
            return None
        with self._lock:
            if self.tripped or self.error:
                return self.tripped
            lo, hi = int(self.s["after_answers"]), int(self.s["max_answers"])
            for row in rows:
                if self.n >= hi:
                    break
                try:
                    if not is_answer(row):
                        continue
                    c = classify(row)
                except Exception as e:  # noqa: BLE001 — fail closed, never a silent pass
                    self.error = (f"early stop could not classify answer {self.n + 1}: "
                                  f"{type(e).__name__}: {e}")[:500]
                    return None
                self.n += 1
                for k in self.counts:
                    self.counts[k] += int(bool(c[k]))
                if c["token_limit"] and row.get("max_tokens_sent"):
                    self.limits.add(int(row["max_tokens_sent"]))
                dlo = int(self.s.get("degenerate_after_answers") or lo)
                if self.n < min(lo, dlo):
                    continue
                rates = self._rates()
                sigs = [(sig, key) for sig, key in TRIP_SIGNALS
                        if self.n >= (dlo if sig == "degenerate" else lo)]
                if self.n >= lo:
                    self.last_eval = {"after_answers": self.n,
                                      **{f"{k}_rate": round(v, 4) for k, v in rates.items()}}
                hits = [(sig, rates[sig], float(self.s[key])) for sig, key in sigs
                        if self.s.get(key) is not None and rates[sig] is not None
                        and rates[sig] > float(self.s[key])]
                if hits:
                    sig, val, thr = hits[0]
                    self.tripped = {"signal": sig, "value": round(val, 4), "threshold": thr,
                                    "after_answers": self.n,
                                    "signals": [h[0] for h in hits],
                                    "counts": dict(self.counts),
                                    "misrouted_rate": round(rates["misrouted"], 4),
                                    "cut_at_max_tokens_sent": sorted(self.limits),
                                    "thresholds": self._thresholds()}
                    return self.tripped
            return None

    def _thresholds(self) -> dict:
        return {k: self.s.get(k) for k in ("after_answers", "max_answers", "blank_rate_gt",
                                           "token_limit_rate_gt", "misrouted_rate_gt",
                                           "degenerate_after_answers", "degenerate_rate_gt")
                } | {"source": self.s["source"]}

    def record(self) -> dict:
        """What the meta carries for a run that was NOT stopped: the counts the
        check saw (misrouted included), or the error that disabled it."""
        out = {"checked": self.enabled, "tripped": False, "answers_seen": self.n,
               **({"skipped": self.skip} if self.skip else {}),
               "counts": dict(self.counts), "thresholds": self._thresholds()}
        if self.last_eval:
            out["last_eval"] = self.last_eval
        if self.error:
            out["error"] = self.error
        elif self.enabled and self.n < int(self.s["after_answers"]):
            out["note"] = "fewer answers than the first window: not judged"
        return out


def targeted_reason(cfg: dict | None, case_ids) -> str | None:
    """Why a run over ``case_ids`` is a targeted rerun (not judged), or None
    for a run start. A run start covers the profile's whole selection
    (``cfg["_profile_case_ids"]``, set by profiles.apply_profile); with no
    profile selection known, the run is judged."""
    full = set((cfg or {}).get("_profile_case_ids") or [])
    have = set(case_ids)
    if not full or full <= have:
        return None
    return f"targeted rerun: {len(have & full)} of {len(full)} profile cases"


def replay(rows: list[dict], cfg: dict | None = None) -> dict:
    """Offline: push stored rows (in their original order) through the same
    check. Returns ``{"tripped": True, **trip record}`` or ``record()``
    (``tripped: False``; ``error`` when a row could not be classified)."""
    es = EarlyStop(cfg)
    rec = es.observe(rows)
    return {"tripped": True, **rec} if rec else es.record()
