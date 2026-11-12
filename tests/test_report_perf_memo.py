"""run_checks memo (2026-11-12 report speed-up): a memoised result must equal
the uncached one, never leak a caller's edits to the next caller, and a
memo/scope-cache hit must not change any verdict."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import session_checks as sc
from crucibleforge.config import load_cases


def _checked_cases():
    return [c for c in load_cases() if c.get("checks")]


def _replies(case: dict) -> list[str]:
    """Deterministic, varied replies built from the case's own text (turns,
    prompt, needles) — enough to exercise passing and failing checks."""
    src = [str(t.get("content") if isinstance(t, dict) else t) for t in case.get("turns") or []]
    src.append(str(case.get("prompt") or case.get("system") or case["id"]))
    n = max([len(case.get("turns") or [1])] + [max(c.get("turns") or [1]) for c in case["checks"]])
    out = []
    for i in range(n):
        base = src[i % len(src)]
        out.append(f'{base}\n"Quoted line {i}," she said. ((OOC: note {i})) You nod.\n'
                   f"## Section\nShe was there. He is here. I can’t write that.")
    if n > 2:
        out[1] = ""  # an empty turn exercises the empty-turn path
    return out


@pytest.fixture(autouse=True)
def _fresh_memo():
    sc.clear_memo()
    yield
    sc.MEMO_ENABLED = True
    sc.clear_memo()


def test_memo_equals_uncached_on_every_checked_case():
    cases = _checked_cases()
    assert cases, "no case carries checks"
    sc.MEMO_ENABLED = False
    want = [sc.run_checks(c, _replies(c)) for c in cases]
    sc.MEMO_ENABLED = True
    sc.clear_memo()
    first = [sc.run_checks(c, _replies(c)) for c in cases]
    again = [sc.run_checks(c, _replies(c)) for c in cases]   # memo hits
    assert first == want
    assert again == want
    assert len(sc._MEMO) > 0


def test_memo_returns_independent_copies():
    case = _checked_cases()[0]
    reps = _replies(case)
    a = sc.run_checks(case, reps)
    snapshot = copy.deepcopy(a)
    a["results"].clear()
    a["groups"]["mutated"] = [9, 9]
    b = sc.run_checks(case, reps)
    assert b == snapshot


def test_memo_key_tells_texts_and_specs_apart():
    case = {"checks": [{"id": "x", "type": "require_regex", "pattern": "dragon", "scope": "all"}]}
    assert sc.run_checks(case, ["a dragon"])["rate"] == 1.0
    assert sc.run_checks(case, ["a cat"])["rate"] == 0.0
    # the same text split differently across turns is a different key
    assert sc._memo_key(case["checks"], ["ab", "c"]) != sc._memo_key(case["checks"], ["a", "bc"])
    other = {"checks": [{"id": "x", "type": "require_regex", "pattern": "cat", "scope": "all"}]}
    assert sc.run_checks(other, ["a cat"])["rate"] == 1.0
    assert sc.run_checks(case, ["a cat"])["rate"] == 0.0


def test_non_string_reply_is_not_memoised():
    case = {"checks": [{"id": "x", "type": "require_regex", "pattern": "x", "scope": "all"}]}
    sc.MEMO_ENABLED = False
    want = sc.run_checks(case, [None, "x"])
    sc.MEMO_ENABLED = True
    assert sc.run_checks(case, [None, "x"]) == want
    assert not sc._MEMO


# ----------------------------------------------- parallel == serial board

def _frozen(monkeypatch):
    from datetime import datetime as _dt

    from crucibleforge import report

    class _Frozen(_dt):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 12, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(report, "datetime", _Frozen)


def _board_files(env, monkeypatch, workers: str) -> dict[str, bytes]:
    from crucibleforge import report
    monkeypatch.setenv("CRUCIBLEFORGE_REPORT_WORKERS", workers)
    sc.clear_memo()
    report._CASES_BY_ID = None
    report.generate(cfg=env.cfg)
    return {p.name: p.read_bytes() for p in sorted(env.results.glob("report*"))
            } | {"failures.md": (env.results / "failures.md").read_bytes()}


def test_parallel_bootstrap_writes_the_same_board_as_serial(tmp_path, monkeypatch, caplog):
    from test_points_board_39 import ALL_WORK, _rows, _setup, _work_only, _write

    from crucibleforge import report
    env = _setup(tmp_path, monkeypatch, _work_only, resamples=60)
    _frozen(monkeypatch)
    weak = ALL_WORK - {"CB-F1-larder-bughunt", "MH06-fermat-mod-prime"}
    _write(env, {"a": _rows(fail_cases={"TZ06-three-parallel-one-turn"}),
                 "b": _rows(fail_cases=weak),
                 "c": _rows(fail={("RX13-recurrence-term", 1)}),
                 "d": _rows(fail_cases={"CB-B6-cron-next"})})
    calls = []
    real = report._parallel_boots

    def spy(jobs):
        calls.append(report.report_workers(len(jobs)))
        return real(jobs)
    monkeypatch.setattr(report, "_parallel_boots", spy)
    serial = _board_files(env, monkeypatch, "1")
    parallel = _board_files(env, monkeypatch, "3")
    assert calls == [1, 3]
    assert "finishing serially" not in caplog.text   # the pool really ran
    assert set(serial) == {"report.md", "report.json", "report.html", "failures.md"}
    assert serial == parallel


def test_report_workers_is_capped():
    from crucibleforge import report
    assert report.report_workers(1) == 1
    assert 1 <= report.report_workers(100) <= 4
