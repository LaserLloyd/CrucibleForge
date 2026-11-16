"""One family/lineage mechanism (crucibleforge/lineage.py): the judge
self-judge rule, the same-family-judge flag and authorship exclusion all read
the same tune map."""
from crucibleforge import judge_elements as JE
from crucibleforge import lineage, report


def test_tunes_resolve_to_their_base_family():
    assert lineage.family("Dark-Scarlett-27B-v3.0-Q8_0_hb16") == "qwen"
    assert lineage.family("dark-scarlett-27b-v3.0-q8-0-hb16") == "qwen"
    assert lineage.family("split-untied-31b-q8") == "google"
    assert lineage.family("omega-arsenal-31b-final") == "google"
    assert lineage.family("swish-31b") == "google"
    assert lineage.family("gemma-4-31B-it") == "google"
    assert lineage.family("zzz-9b") is None
    # not resolving tunes: the literal id decides
    assert lineage.family("split-untied-31b", tunes=False) is None


def test_self_judge_rule_covers_tunes():
    # a Gemma judge never judges a Gemma tune; a Qwen judge never judges Dark-Scarlett
    assert JE.same_family("gemma-4-31B-it-uncensored-heretic", ["Split-Untied-31B-q8"])
    assert JE.same_family("qwen3.5-122b-heretic", ["Dark-Scarlett-27B-v3.0"])
    assert not JE.same_family("gemma-4-31B-it-uncensored-heretic", ["Dark-Scarlett-27B-v3.0"])
    assert not JE.same_family("deepseek-v4-flash", ["Split-Untied-31B-q8"])
    cfg = {"judge": {"candidates": [{"model_id": "gemma-4-31B-heretic"},
                                    {"model_id": "qwen3.5-122b"},
                                    {"model_id": "deepseek-v4-flash"}]}}
    assert JE.family_excluded_ids(cfg, {"dark-scarlett-27b-v3.0"}, None) == {"qwen3.5-122b"}
    assert JE.family_excluded_ids(cfg, {"split-untied-31b"}, None) == {"gemma-4-31B-heretic"}


def test_one_table_feeds_every_consumer():
    assert JE.FAMILIES is lineage.FAMILIES
    assert report.LINEAGE_FAMILIES is lineage.SIBLINGS
    assert report._row_family("Dark-Scarlett-27B-v3.0") == "qwen"
    assert report._row_family("grok-4.7") == "grok"
    assert report.same_family_judge({"model_id": "split-untied-31b", "judge_model": "gemma-4-31b-heretic"})
    assert not report.same_family_judge({"model_id": "dark-scarlett-27b", "judge_model": "gemma-4-31b"})


def test_authorship_keeps_its_narrow_rule(monkeypatch):
    def case(prose):
        return {"meta": {"authorship": {"prose": prose}}}
    monkeypatch.setattr(report, "_CASES_BY_ID", {"G": case(["gemma-26b-qat"]),
                                                  "T": case(["split-untied-31b"]),
                                                  "Q": case(["qwen3.8-27b"])})
    ids = ["G", "T", "Q"]
    # a tune is excluded from its siblings' cases, not (by default) from its base family's
    assert lineage.AUTHORSHIP_RESOLVES_TUNES is False
    assert report.authored_cases_for("swish-31b", ids) == ["T"]
    # a plain Gemma is not excluded from a case a tune wrote
    assert report.authored_cases_for("gemma-x", ids) == ["G"]
    # widening the switch resolves tunes on the model side (Dark-Scarlett -> Qwen lineage)
    monkeypatch.setattr(lineage, "AUTHORSHIP_RESOLVES_TUNES", True)
    assert report.authored_cases_for("Dark-Scarlett-27B-v3.0", ids) == ["Q"]
    assert report.authored_cases_for("split-untied-31b", ids) == ["G", "T"]
