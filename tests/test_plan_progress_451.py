"""Docker assessment 2026-11-25, item 2: `all --plan` prints a first line at once and a progress line at least every
10 seconds (stderr; the plan on stdout is unchanged), and the plan's checks never render a generated case to read
keys no generator returns."""
from __future__ import annotations

import io
import time

from crucibleforge import adult_guard, cli, runner
from crucibleforge.lazycase import ABSENT_KEYS, LazyCase


def test_the_ticker_says_a_first_line_at_once_and_then_keeps_talking():
    buf = io.StringIO()
    assert cli.PLAN_TICK_S <= 10
    t = cli.PlanTicker(every=0.05, stream=buf).start(1)
    assert buf.getvalue().startswith("plan: computing what `all` would run")
    t.phase("m: ladder sets")
    time.sleep(0.3)
    t.stop()
    lines = buf.getvalue().splitlines()
    assert any(ln.startswith("plan: still working (") and ln.endswith("m: ladder sets") for ln in lines[1:])
    n = len(lines)
    time.sleep(0.15)
    assert len(buf.getvalue().splitlines()) == n                      # silent once stopped


def test_the_plan_checks_do_not_render_a_generated_case():
    calls = []
    c = LazyCase({"id": "X-1", "category": "coding", "generator": {"type": "x"}}, lambda d: calls.append(d))
    assert not adult_guard.is_adult(c)
    assert runner.fixed_budget({"defaults": {}}, c) is None
    assert runner.wide_context({"defaults": {}}, [c]) is None
    assert calls == [] and not c.rendered


def test_a_generator_that_returns_an_absent_key_fails_closed():
    import pytest
    k = sorted(ABSENT_KEYS)[0]
    c = LazyCase({"id": "X-2", "category": "reasoning", "generator": {"type": "x"}}, lambda d: d.update({k: 1}))
    with pytest.raises(ValueError):
        c.render()


def _fill(d):
    d["prompt"] = f"p-{d['id']}"
    d["max_tokens"] = 1000 + len(d["id"])


def test_prefetch_renders_in_workers_exactly_as_in_process():
    def mk(i):
        c = LazyCase({"id": f"G-{i}", "category": "reasoning", "generator": {"type": "x"}}, _fill)
        c["repeats"] = 2                                       # a write before the render is replayed after it
        return c
    a, b = [mk(i) for i in range(4)], [mk(i) for i in range(4)]
    from crucibleforge.lazycase import prefetch
    assert prefetch(a, workers=2) == 4
    for x, y in zip(a, b):
        assert x.rendered and dict(x) == dict(y.render())
