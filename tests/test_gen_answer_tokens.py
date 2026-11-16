"""Every family row carries answer_tokens (median reference-answer tokens, chars/4, 30 seeds)."""
import statistics
import pytest
from crucibleforge import gen
from crucibleforge.gen._stack_common import TURN_SEP

VARIANTS = {"istack": ["write", "conv"], "session_state": ["state", "multi"]}


def _median(fam, lv, var):
    m = gen.get(fam)
    xs = []
    for i in range(30):
        g = {"type": fam, "level": lv}
        if var:
            g["variant"] = var
        r = m.reference(g, release_seed="meas", case_id=f"X{i}")
        xs.append(len(r.replace(TURN_SEP, "")) / 4)
    return statistics.median(xs)


@pytest.mark.parametrize("fam", ["keyfunc", "tracevm", "ledger", "istack", "session_state"])
def test_answer_tokens_declared_and_measured(fam):
    m = gen.get(fam)
    for lv, row in m.LEVELS.items():
        assert row.get("answer_tokens", 0) > 0, (fam, lv)
        for var in VARIANTS.get(fam, [None]):
            want = row.get("answer_tokens_by_variant", {}).get(var, row["answer_tokens"])
            got = _median(fam, lv, var)
            assert abs(got - want) <= max(3, 0.1 * want), (fam, lv, var, got, want)
