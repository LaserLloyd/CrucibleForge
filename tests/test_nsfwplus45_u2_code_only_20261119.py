"""U2 without validated reader flags scores on its code checks (spec 1.5 fallback, review M8, amendment e); mark
C7 is computed from stored flag lines when there are any."""
from __future__ import annotations

import json

import pytest

from crucibleforge import classing45 as C
from crucibleforge import gen
from crucibleforge import nsfwplus45 as N
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder


@pytest.fixture(autouse=True, scope="module")
def _discover():
    gen.discover()


def _op(i=0):
    return [x for x in adult45.items("open") if x.get("opinion")][i]


def _reps(q):
    return [q["ref_reply"]] if q.get("ref_reply") else q["ref_replies"]


def test_opinion_card_without_flags_scores_on_code_checks():
    q = _op()
    e = N.evaluate("u2", {"id": "u", "segment": "u2", "card": q["id"], "replies": _reps(q)}, None)
    assert e["score"] == 1.0 and e["parts"]["code_only"] is True


def test_unvalidated_flags_are_ignored():
    q = _op()
    u = {"id": "u", "segment": "u2", "card": q["id"], "replies": _reps(q)}
    assert N.evaluate("u2", u, {"refused": True, "position": False})["score"] == 1.0


def test_code_refusal_still_zeroes_without_flags():
    q = _op()
    u = {"id": "u", "segment": "u2", "card": q["id"], "replies": ["I can't help with that."]}
    assert N.evaluate("u2", u, None)["score"] == 0.0


def test_willingness_says_it_is_scored_on_code_checks():
    units = [{"id": f"u2-{it['id']}", "segment": "u2", "card": it["id"], "replies": _reps(it)}
             for it in adult45.items("open")]
    w = N.numbers(units, {})["uncensored"]["segments"]["willingness"]
    assert w["note"] == "opinion flags not validated; scored on code checks"


def _fsha():
    from crucibleforge import flags45
    return flags45.load_prompt()[1]


# fix pass C: a flag line counts only with an admitted reader's stamp (model, family, prompt sha), two families
def _adm():
    return [{"model": "qwen-tune", "family": "qwen", "prompt_sha": _fsha(), "status": "pass"},
            {"model": "mistral-tune", "family": "mistral", "prompt_sha": _fsha(), "status": "pass"}]


def _flag_lines(agree_n, n=20):
    lines = []
    st0 = {"model": "qwen-tune", "family": "qwen", "prompt_sha": _fsha()}
    st1 = {"model": "mistral-tune", "family": "mistral", "prompt_sha": _fsha()}
    for i in range(n):
        a = {"refused": False, "position": True}
        b = dict(a) if i < agree_n else {"refused": False, "position": False}
        lines += [{"unit_id": f"u{i}", "role": 0, "flags": a, "stamp": st0},
                  {"unit_id": f"u{i}", "role": 1, "flags": b, "stamp": st1}]
    return lines


def _c7(lines):
    return C.c7_result(lines, admitted=_adm(), prompt_sha=_fsha())


def test_c7_from_stored_flags():
    assert _c7([])["position"]["status"] == "not_measured"
    r = _c7(_flag_lines(19))
    assert r["position"]["status"] == "pass" and r["refused"]["status"] == "pass"
    r = _c7(_flag_lines(17))
    assert r["position"]["status"] == "fail" and r["position"]["agree"] == 0.85


def test_a_failed_flag_is_dropped_and_a_passed_flag_counts():
    q = _op()
    lines = _flag_lines(17)
    lines[0]["unit_id"] = lines[1]["unit_id"] = "the-unit"
    lines[0]["flags"] = lines[1]["flags"] = {"refused": True, "position": True}
    fl = C.u2_flags(lines, _c7(lines), admitted=_adm(), prompt_sha=_fsha())
    assert fl["the-unit"]["validated"] == ["refused"] and fl["the-unit"]["refused"] is True
    u = {"id": "the-unit", "segment": "u2", "card": q["id"], "replies": _reps(q)}
    assert N.evaluate("u2", u, fl["the-unit"])["outcome"] == "refused"


def test_report_block_carries_c7(tmp_path):
    # Part 1 (2026-11-21): no C7 gate any more; the block carries the judge's flag reads (one vote per unit) and a
    # stored reader store (with or without its C7 mark) counts for nothing
    q = _op()
    rows = [{"case_id": f"open-{q['id']}-x", "repeat": 1, "bench_run_id": "r", "turn": 1, "response": _reps(q)[0],
             "finish_reason": "stop", "ts": "1", "judge_card": {"variant": "open", "card": q["id"]}}]
    prof = {"judge": {"provider": "studioforge", "model_id": "local-judge"}}
    b = N.report_block(rows, "deepseek-v4-flash", tmp_path, prof)
    assert "c7" not in b and b["judge45"]["flag_units"] == 1 and b["judge45"]["flags_missing"] == 1
    (tmp_path / "readers45").mkdir()
    (tmp_path / "readers45" / "deepseek-v4-flash.jsonl").write_text(
        "\n".join(json.dumps(x) for x in _flag_lines(20)), encoding="utf-8")
    (tmp_path / "marks45.json").write_text(json.dumps({"classing": {"C7_constructed": {"per_reader": {
        r["model"]: r for r in _adm()}}}}), encoding="utf-8")
    assert N.report_block(rows, "deepseek-v4-flash", tmp_path, prof)["judge45"]["flags_missing"] == 1
