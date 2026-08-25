"""Unit tests for objective graders — no network, no LM Studio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crucibleforge import graders
from crucibleforge.api import ChatResult


# ---------------------------------------------------------- code extraction
def test_extract_python_prefers_largest_fence():
    text = "Here:\n```python\nx = 1\n```\nand\n```python\ndef f():\n    return 42\n```"
    code = graders.extract_python(text)
    assert "return 42" in code
    assert "x = 1" not in code


def test_extract_python_bare_fallback():
    assert graders.extract_python("def f(): return 1") == "def f(): return 1"


# ------------------------------------------------------------- python_exec
def _res(text="", tool_calls=None):
    r = ChatResult()
    r.response_text = text
    r.tool_calls = tool_calls or []
    return r


def test_python_exec_pass():
    case = {"grader_config": {"tests": "assert fizz(3) == 'Fizz'"}}
    resp = "```python\ndef fizz(n):\n    return 'Fizz' if n % 3 == 0 else str(n)\n```"
    out = graders.grade_python_exec(resp, case["grader_config"])
    assert out["grade"] == "pass"


def test_python_exec_fail_assertion():
    resp = "```python\ndef fizz(n):\n    return 'wrong'\n```"
    out = graders.grade_python_exec(resp, {"tests": "assert fizz(3) == 'Fizz'"})
    assert out["grade"] == "fail"


def test_python_exec_no_code():
    # Truly empty extraction -> "no code"; prose-only refusal -> syntax error.
    # Both must fail.
    empty = graders.grade_python_exec("", {"tests": "assert True"})
    assert empty["grade"] == "fail" and "no code" in empty["detail"]
    prose = graders.grade_python_exec("I cannot help with that.", {"tests": "assert True"})
    assert prose["grade"] == "fail"


def test_python_exec_timeout():
    resp = "```python\ndef f():\n    while True:\n        pass\n```"
    out = graders.grade_python_exec(resp, {"tests": "f()"})
    assert out["grade"] == "fail"
    assert "timeout" in out["detail"]


def test_python_exec_syntax_error():
    out = graders.grade_python_exec("```python\ndef f( :\n```", {"tests": "pass"})
    assert out["grade"] == "fail"


def test_python_exec_cannot_import_network():
    # -I isolates but does not block imports; ensure a normal solution still runs.
    resp = "```python\nimport math\ndef area(r):\n    return math.pi * r * r\n```"
    out = graders.grade_python_exec(resp, {"tests": "assert round(area(1),2) == 3.14"})
    assert out["grade"] == "pass"


# --------------------------------------------------------------- tool_call
def test_tool_call_correct():
    calls = [{"name": "get_weather", "arguments": '{"location": "Paris, France"}'}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "get_weather",
                                              "required_args": {"location": ["paris"]}})
    assert out["grade"] == "pass"


def test_tool_call_wrong_tool():
    calls = [{"name": "send_message", "arguments": "{}"}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "get_weather"})
    assert out["grade"] == "fail"


def test_tool_call_missing_arg():
    calls = [{"name": "get_weather", "arguments": '{"unit": "celsius"}'}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "get_weather",
                                              "required_args": {"location": ["paris"]}})
    assert out["grade"] == "fail"
    assert "location" in out["detail"]


def test_tool_call_bad_json_args():
    calls = [{"name": "get_weather", "arguments": "{not json}"}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "get_weather"})
    assert out["grade"] == "fail"


def test_tool_call_expected_none_but_called():
    calls = [{"name": "get_weather", "arguments": "{}"}]
    out = graders.grade_tool_call(calls, "", {"expect_no_tool": True})
    assert out["grade"] == "fail"


def test_tool_call_expected_none_answered():
    out = graders.grade_tool_call([], "The answer is 42.", {"expect_no_tool": True,
                                                            "answer_contains": ["42"]})
    assert out["grade"] == "pass"


def test_tool_call_arg_value_mismatch():
    calls = [{"name": "send_message", "arguments": '{"recipient": "Bob", "body": "hi"}'}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "send_message",
                                              "required_args": {"recipient": ["alice"]}})
    assert out["grade"] == "fail"


# ------------------------------------------------------------------ checks
def test_checks_exact_pass_and_fail():
    assert graders.grade_checks("Hello", {"checks": [{"type": "exact", "value": "Hello"}]})["grade"] == "pass"
    # surrounding whitespace is stripped before comparison
    assert graders.grade_checks("  Hello  ", {"checks": [{"type": "exact", "value": "Hello"}]})["grade"] == "pass"
    # but exact is case-sensitive
    assert graders.grade_checks("hello", {"checks": [{"type": "exact", "value": "Hello"}]})["grade"] == "fail"
    assert graders.grade_checks("Goodbye", {"checks": [{"type": "exact", "value": "Hello"}]})["grade"] == "fail"


def test_checks_lowercase():
    assert graders.grade_checks("all lower.", {"checks": [{"type": "lowercase"}]})["grade"] == "pass"
    assert graders.grade_checks("Has Upper", {"checks": [{"type": "lowercase"}]})["grade"] == "fail"


def test_checks_line_count_prefix():
    good = "- red\n- green\n- blue"
    assert graders.grade_checks(good, {"checks": [{"type": "line_count_prefix", "n": 3, "prefix": "- "}]})["grade"] == "pass"
    bad = "- red\n- green"
    assert graders.grade_checks(bad, {"checks": [{"type": "line_count_prefix", "n": 3, "prefix": "- "}]})["grade"] == "fail"
    noprefix = "red\ngreen\nblue"
    assert graders.grade_checks(noprefix, {"checks": [{"type": "line_count_prefix", "n": 3, "prefix": "- "}]})["grade"] == "fail"


def test_checks_ends_with():
    assert graders.grade_checks("the sea is deep.", {"checks": [{"type": "ends_with", "value": "deep."}]})["grade"] == "pass"


def test_checks_json_object_types():
    resp = '{"name": "Ada", "age": 36, "active": true}'
    cfg = {"checks": [{"type": "json_object", "keys": {"name": "str", "age": "int", "active": "bool"}}]}
    assert graders.grade_checks(resp, cfg)["grade"] == "pass"


def test_checks_json_object_wrong_type():
    resp = '{"name": "Ada", "age": "36", "active": true}'
    cfg = {"checks": [{"type": "json_object", "keys": {"age": "int"}}]}
    assert graders.grade_checks(resp, cfg)["grade"] == "fail"


def test_checks_json_object_in_fence():
    resp = '```json\n{"x": 1}\n```'
    cfg = {"checks": [{"type": "json_object", "keys": {"x": "int"}}]}
    assert graders.grade_checks(resp, cfg)["grade"] == "pass"


def test_checks_bool_not_int():
    # In Python bool is a subclass of int; ensure an int isn't accepted as bool.
    resp = '{"active": 1}'
    cfg = {"checks": [{"type": "json_object", "keys": {"active": "bool"}}]}
    assert graders.grade_checks(resp, cfg)["grade"] == "fail"


def test_checks_max_words():
    assert graders.grade_checks("one two three", {"checks": [{"type": "max_words", "n": 5}]})["grade"] == "pass"
    assert graders.grade_checks("a b c d e f", {"checks": [{"type": "max_words", "n": 5}]})["grade"] == "fail"


# ----------------------------------------------------------------- numeric
def test_numeric_answer_line():
    assert graders.grade_numeric("Work...\nAnswer: 24", {"answer": 24})["grade"] == "pass"


def test_numeric_last_number_fallback():
    assert graders.grade_numeric("so it costs 24 dollars", {"answer": 24})["grade"] == "pass"


def test_numeric_with_commas():
    assert graders.grade_numeric("Answer: 1,994", {"answer": 1994})["grade"] == "pass"


def test_numeric_wrong():
    assert graders.grade_numeric("Answer: 30", {"answer": 24})["grade"] == "fail"


def test_numeric_none_found():
    assert graders.grade_numeric("no digits here", {"answer": 24})["grade"] == "fail"


def test_numeric_prefers_answer_line_over_stray_numbers():
    text = "First I had 100 apples and 5 baskets.\nAnswer: 20"
    assert graders.grade_numeric(text, {"answer": 20})["grade"] == "pass"


# ---------------------------------------------------------------- contains
def test_contains():
    assert graders.grade_contains("The answer is Casey.", {"needles": ["Casey"]})["grade"] == "pass"
    assert graders.grade_contains("nope", {"needles": ["Casey"]})["grade"] == "fail"


# ------------------------------------------------------------ refusal heuristic
def test_refusal_heuristic_true():
    assert graders.refusal_heuristic("I can't write that kind of content.") is True


def test_refusal_heuristic_typographic_apostrophe():
    assert graders.refusal_heuristic("I can’t write explicit content.") is True


def test_refusal_heuristic_false_on_in_character_dialogue():
    # v1 bug: "i won't" inside erotica dialogue was flagged as a refusal.
    scene = ("She pressed close. \"Tell me to stop,\" he breathed, \"or I won't "
             "be able to.\" The rain drummed on as they came together...")
    assert graders.refusal_heuristic(scene) is False


def test_refusal_heuristic_ignores_late_mention():
    # A marker far past the window doesn't count (real refusals are up front).
    text = "x" * 500 + " i cannot write this"
    assert graders.refusal_heuristic(text) is False


def test_grade_dispatch_none_for_judged():
    assert graders.grade(_res("whatever"), {"category": "nsfw", "rubric": "nsfw"}) is None


def test_grade_dispatch_runs_grader():
    case = {"grader": "contains", "grader_config": {"needles": ["hi"]}}
    assert graders.grade(_res("well hi there"), case)["grade"] == "pass"


# ------------------------------------ audit-fix regression tests (graders)
def test_python_exec_sys_exit_cannot_fake_pass():
    # A wrong solution that calls sys.exit(0) before tests must still FAIL.
    resp = "```python\ndef two_sum(nums, t):\n    return [9, 9]\nimport sys; sys.exit(0)\n```"
    out = graders.grade_python_exec(resp, {"tests": "assert two_sum([2,7],9)==[0,1]"})
    assert out["grade"] == "fail", out


def test_python_exec_top_level_exit_swallowed_then_tested():
    # sys.exit in solution is swallowed; a CORRECT function then passes.
    resp = "```python\ndef add(a,b):\n    return a+b\nimport sys; sys.exit(0)\n```"
    out = graders.grade_python_exec(resp, {"tests": "assert add(2,3)==5"})
    assert out["grade"] == "pass", out


def test_python_exec_network_blocked_under_sandbox():
    # Under bwrap --unshare-all there is no network; a solution that opens a
    # socket to the internet must fail (not hang, not pass).
    if not graders._BWRAP:
        import pytest
        pytest.skip("no bwrap")
    resp = ("```python\nimport socket\n"
            "def f():\n    s=socket.create_connection(('1.1.1.1',53),timeout=3)\n    return 1\n```")
    out = graders.grade_python_exec(resp, {"tests": "assert f()==1"})
    assert out["grade"] == "fail"


def test_prose_metrics_slop_and_repetition():
    m = graders.prose_metrics("a b c a b c a b c")  # trigram 'a b c' repeats
    assert m["repetition"] > 0
    slop = graders.prose_metrics("a shiver ran down her spine, barely above a whisper")
    assert slop["slop_per_1k"] > 0


def test_contains_answer_line_only():
    # 'Casey' appears in reasoning but the Answer line says Blair -> fail.
    text = "Alex isn't fish, Casey could be... let me check.\nAnswer: Blair"
    assert graders.grade_contains(text, {"needles": ["Casey"], "answer_line": True})["grade"] == "fail"
    text2 = "Reasoning...\nAnswer: Casey owns the fish"
    assert graders.grade_contains(text2, {"needles": ["Casey"], "answer_line": True})["grade"] == "pass"


def test_tool_call_rejects_spurious_extra_calls():
    calls = [{"name": "get_weather", "arguments": '{"location":"Paris"}'},
             {"name": "get_weather", "arguments": '{"location":"London"}'}]
    out = graders.grade_tool_call(calls, "", {"expect_tool": "get_weather"})
    assert out["grade"] == "fail" and "2 tool calls" in out["detail"]


# ------------------------------- thinking-tag leak defense (deliverable 1)
def test_strip_thinking_tags_deepseek():
    assert graders.strip_thinking_tags("<think>let me solve this</think>42") == "42"


def test_strip_thinking_tags_gemma_paired():
    assert graders.strip_thinking_tags("(think) hmm, carefully...(think) Sure thing!") == " Sure thing!"


def test_strip_thinking_tags_qwen3():
    assert graders.strip_thinking_tags("<|thinking|>compute</|thinking|>7") == "7"


def test_strip_thinking_tags_unclosed_drops_remainder():
    assert graders.strip_thinking_tags("intro <think> reasoning never closed") == "intro "


def test_strip_thinking_tags_stray_closer():
    assert graders.strip_thinking_tags("</think>final answer") == "final answer"


def test_strip_thinking_tags_multiple_blocks():
    text = "<think>a</think>one (think)b(think) two <|thinking|>c</|thinking|> three"
    assert graders.strip_thinking_tags(text) == "one  two  three"


def test_strip_thinking_tags_idempotent():
    t = "<think>a</think>42"
    assert graders.strip_thinking_tags(graders.strip_thinking_tags(t)) == "42"


def test_strip_thinking_tags_plain_text_unchanged():
    assert graders.strip_thinking_tags("plain text") == "plain text"
    assert graders.strip_thinking_tags("") == ""


def test_extract_python_ignores_think_fence():
    # a leaked (think) block carrying its own LONGER fence must not win: the
    # real answer fence is what gets exec'd (fixes "unterminated string
    # literal" — previously the reasoning snippet was picked and exec'd)
    text = ("(think)```python\n"
            "def broken():\n"
            "    return 'unterminated\n"
            "```(think)\n"
            "```python\ndef f():\n    return 42\n```")
    code = graders.extract_python(text)
    assert "return 42" in code
    assert "unterminated" not in code


def test_tool_call_text_fallback_json_shape():
    # thinking model printed the call as text with tags around it
    text = ('<think>I need the weather API</think>'
            '{"name": "get_weather", "arguments": {"location": "Paris"}}')
    out = graders.grade_tool_call([], text, {"expect_tool": "get_weather",
                                             "required_args": {"location": ["paris"]}})
    assert out["grade"] == "pass"


def test_tool_call_text_fallback_shorthand():
    out = graders.grade_tool_call([], 'get_weather({"location": "Paris"})',
                                  {"expect_tool": "get_weather",
                                   "required_args": {"location": ["paris"]}})
    assert out["grade"] == "pass"


def test_tool_call_text_fallback_wrong_tool():
    out = graders.grade_tool_call([], '{"name": "send_message", "arguments": {}}',
                                  {"expect_tool": "get_weather"})
    assert out["grade"] == "fail"
    assert "send_message" in out["detail"]


def test_tool_call_expected_none_detects_text_call():
    out = graders.grade_tool_call([], '{"name": "get_weather", "arguments": {}}',
                                  {"expect_no_tool": True})
    assert out["grade"] == "fail"


def test_tool_parallel_text_fallback():
    text = ('{"name": "a", "arguments": {"x": "1"}} and '
            '{"name": "b", "arguments": {"y": "2"}}')
    cfg = {"expect_calls": [{"name": "a", "required_args": {"x": ["1"]}},
                            {"name": "b", "required_args": {"y": ["2"]}}]}
    out = graders.grade_tool_parallel([], cfg, text)
    assert out["grade"] == "pass"


def test_checks_json_object_after_think():
    resp = '<think>let me output json</think>{"name": "test"}'
    out = graders.grade_checks(resp, {"checks": [{"type": "json_object",
                                                  "keys": {"name": "str"}}]})
    assert out["grade"] == "pass"


def test_checks_exact_after_think():
    out = graders.grade_checks("<think>hmm</think>Hello",
                               {"checks": [{"type": "exact", "value": "Hello"}]})
    assert out["grade"] == "pass"
