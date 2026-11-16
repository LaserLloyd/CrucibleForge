"""scripts/budget_thinkoff.py: the budget reference must come from thinking-off runs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import budget_thinkoff as bt  # noqa: E402


def r(reason_tokens=0, text=""):
    return {"metrics": {"reasoning_tokens": reason_tokens}, "reasoning": text}


def test_classify():
    assert bt.classify([r(), r(), r()])["verdict"] == "thinking-off"
    assert bt.classify([r(120), r(), r()])["verdict"] == "THINKING"      # tokens
    assert bt.classify([r(0, "let me think")])["verdict"] == "THINKING"  # text only
    assert bt.classify([r(0, "   ")])["verdict"] == "thinking-off"
    assert bt.classify([])["verdict"] == "missing"
    assert bt.classify([r(5)] + [r()] * 99, max_share=0.02)["verdict"] == "thinking-off"  # 1% <= 2%


def test_evaluate_fails_on_any_thinker_or_missing_or_empty():
    by = {"a": [r()], "b": [r(9)], "c": []}
    assert bt.evaluate(by, ["a"])["ok"]
    out = bt.evaluate(by, ["a", "b", "c"])
    assert not out["ok"] and out["not_thinking_off"] == ["b", "c"]
    assert not bt.evaluate(by, [])["ok"]
