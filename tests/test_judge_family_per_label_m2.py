"""M2 (Opus review 4.5): the family self-judge exclusion is per label, not per run."""
from __future__ import annotations

from crucibleforge import judge, judge_elements


class _Stop(Exception):
    pass


def _cfg():
    return {
        "models": [{"name": "gem", "model_id": "gemma-4-26B-qat"},
                   {"name": "qw", "model_id": "qwen3.5-122b"}],
        "judge": {"candidates": [{"model_id": "gemma-4-31B-it"}, {"model_id": "fallback-x"}],
                  "samples": 1},
        "judge_policy": {"mode": "elements"},
    }


def test_exclusion_is_per_label(monkeypatch, tmp_path):
    from crucibleforge import config
    config.set_results_dir(tmp_path)
    seen = []

    def fake_load(cfg, benched, **kw):
        seen.append(set(benched))
        raise _Stop

    monkeypatch.setattr(judge, "_load_judge", fake_load)
    monkeypatch.setattr(judge, "_pending_rows",
                        lambda label, *a, **k: [{"case_id": "C", "rubric": "rp_single"}])
    try:
        judge.run_judge(_cfg(), ["gem", "qw"])
    except _Stop:
        pass
    # the Gemma label excludes the Gemma-family judge; the Qwen label does not
    assert any("gemma-4-26B-qat" in s and "gemma-4-31B-it" in s for s in seen), seen
    qw = [s for s in seen if "qwen3.5-122b" in s]
    assert qw and all("gemma-4-31B-it" not in s and "gemma-4-26B-qat" not in s for s in qw), seen


def test_l2_willing_card_regex_reaches_assess(monkeypatch):
    """willing_refusal_regex on the CASE (not copied onto the row) refuses the row."""
    monkeypatch.setattr(judge, "_CASE_FIELDS", {"WX": {"willing_refusal_regex": [r"^as a character i won't"]}})
    monkeypatch.setattr(judge, "_CASE_ELEMENTS", {"WX": []})
    row = {"case_id": "WX", "response": "As a character I won't go there, sorry."}
    assert judge_elements.willingness_verdict(row)["refused"] is True


def test_groups_follow_the_exclusion_set_not_the_model(monkeypatch, tmp_path):
    """N4: models with an identical exclusion set share one judge phase (one load + canary)."""
    from crucibleforge import config
    config.set_results_dir(tmp_path)
    cfg = _cfg()
    cfg["models"] = [{"name": n, "model_id": m} for n, m in
                     [("a", "llama-3-70b"), ("b", "qwen3.5-122b"), ("c", "mistral-large"),
                      ("g", "gemma-4-26B-qat")]]
    loads = []

    def fake_load(cfg_, benched, **kw):
        loads.append(frozenset(benched))
        raise _Stop

    monkeypatch.setattr(judge, "_load_judge", fake_load)
    monkeypatch.setattr(judge, "_pending_rows",
                        lambda label, *a, **k: [{"case_id": "C", "rubric": "rp_single"}])
    try:
        judge.run_judge(cfg, ["a", "b", "c", "g"])
    except _Stop:
        pass
    assert sorted(sorted(x for x in s_ if x in {"llama-3-70b", "qwen3.5-122b", "mistral-large", "gemma-4-26B-qat"})
                  for s_ in loads) == [["gemma-4-26B-qat"], ["llama-3-70b", "mistral-large", "qwen3.5-122b"]], loads
    assert len(loads) == 2, loads
