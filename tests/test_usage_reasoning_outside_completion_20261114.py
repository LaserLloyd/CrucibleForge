"""xAI reports reasoning beside completion_tokens (total = prompt + completion + reasoning, billed as output):
the harness folds it in, from the usage block alone (2026-11-14, Grok pair-3 field run)."""
from crucibleforge import api, spend

XAI = {"prompt_tokens": 208, "completion_tokens": 1, "total_tokens": 513,
       "completion_tokens_details": {"reasoning_tokens": 304}}            # a real grok-4.7 usage block
OPENAI = {"prompt_tokens": 208, "completion_tokens": 305, "total_tokens": 513,
          "completion_tokens_details": {"reasoning_tokens": 304}}         # reasoning already inside completion


def test_reasoning_outside_completion_is_folded_in():
    assert api.completion_with_reasoning(XAI) == 305


def test_reasoning_inside_completion_is_not_double_counted():
    assert api.completion_with_reasoning(OPENAI) == 305
    assert api.completion_with_reasoning({"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}) == 9
    assert api.completion_with_reasoning({"completion_tokens": 9,
                                          "completion_tokens_details": {"reasoning_tokens": 4}}) == 9  # no total


def test_cost_charges_the_reasoning_at_the_output_rate():
    r = api.ChatResult(prompt_tokens=208, completion_tokens=api.completion_with_reasoning(XAI))
    assert abs(spend.call_cost({"input": 2, "output": 6}, r) - (208 * 2 + 305 * 6) / 1e6) < 1e-12
