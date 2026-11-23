import json
import pytest
from crucibleforge import rplong_mean as M

R = M.load_readers(M.READERS_CONFIG)


def test_scene_score_is_the_mean_of_two():
    assert M.scene_score(0.8, 0.6) == pytest.approx(0.7)
    assert M.scene_score({"a": True, "b": False}, {"a": True, "b": True}) == pytest.approx(0.75)
    assert M.scene_score({"a": {"credit": 1, "weight": 3}, "b": False}, 1.0) == pytest.approx((0.75 + 1) / 2)
    assert M.scene_score(0.8, None) is None


def test_reader_family_rules():
    qwen, deep = M.ReaderSpec("q", "qwen"), M.ReaderSpec("d", "deepseek")
    assert M.check_fit(qwen, "qwen")                       # graded family
    assert M.check_fit(M.ReaderSpec("g", "google"), "qwen")  # judge family
    assert M.check_fit(deep, "deepseek")
    assert M.check_fit(M.ReaderSpec(None, "qwen"), "xai")
    assert M.check_fit(M.ReaderSpec("t", "mistral", thinking=False), "xai")
    assert M.check_fit(deep, "qwen") is None


def test_pick_reader_never_same_family_and_falls_back_hosted():
    for gf in ("qwen", "xai", "minimax", "openai"):
        r = M.pick_reader(gf, R)
        assert r.family not in (gf, "google")
        assert r.hosted == (gf == "qwen")                             # only R-Q (qwen) is named locally; a qwen row falls back
    # lead decision f (2026-11-19): Grok is not a reader; DeepSeek rows are read by local readers only
    assert M.pick_reader("deepseek", R).id == "qwen3.8-27b-ultra-heretic-nvfp4"     # R-Q is named by the 2026-11-20b addendum
    with pytest.raises(M.NoFittingReader):
        M.pick_reader("qwen", R, allow_hosted=False)
    with pytest.raises(M.NoFittingReader):
        M.pick_reader("qwen", R, unavailable={"deepseek-flash"})


def test_local_first_when_named():
    cfg = M.load_readers({"local": [{"role": "R-M", "id": "mistral-x", "family": "mistral"}],
                          "hosted": [{"id": "deepseek-v4-flash", "family": "deepseek"}]})
    assert M.pick_reader("qwen", cfg).id == "mistral-x" and not M.pick_reader("qwen", cfg).hosted
    assert M.pick_reader("mistral", cfg).hosted


def _units(scores):
    return {f"r|{c}|1": {"case": c, "segment": M.SCENES[c][0], "status": "ok" if v is not None else "pending",
                         "mean": v, "reason": None if v is not None else "not read by a second reader"}
            for c, v in scores.items()}


def test_segment_points_and_pending():
    full = {c: 0.5 for c in M.SCENES}
    seg = M.segment_points(_units(full))
    assert seg["character"]["points"] == pytest.approx(0.5) and seg["remembers"]["points"] == pytest.approx(1.0)
    assert seg["cowrites"]["points"] == pytest.approx(0.75)
    part = dict(full, **{"WR2-red-thread-key": None})
    seg = M.segment_points(_units(part))
    assert not seg["cowrites"]["counted"] and seg["cowrites"]["points"] is None
    lines = M.board_lines(seg)
    assert any("Co-writes to a brief" in x and "WR2-red-thread-key" in x for x in lines)
    assert M.board_lines(seg, {"hosted": True}, {"measured": True, "pass": False, "reason": "judge term 6.1% > 5%"})[-2:] == [
        "Long scenes read by hosted reader.", "Long scenes pending: judge term 6.1% > 5%."]


def test_scene_scores_counts_only_the_pinned_reader_and_unread():
    case = "CC3-tea-hut"
    row = {"case_id": case, "rubric": "rp_deep", "bench_run_id": "r", "repeat": 1,
           "judge": {"instrument": {"rubric_sha": "abc"}, "elements": {"results": [{"id": "a", "pass": True, "weight": 1}]}},
           "conversation": [{"content": "x"}]}
    from crucibleforge import agree as AG
    sha = AG.row_sha(row)
    mk = lambda fam, rid: {"part": AG.LONG_PART, "model": "m", "unit": "r|CC3-tea-hut|1", "row_sha": sha,
                           "reader": rid, "score_reader": 0.5,
                           "reader_stamp": {"family": fam, "model": rid, "prompt_sha": "abc"}}
    kw = dict(label="m", cases={}, graded_family="qwen", readers=R)
    sc = M.scene_scores([row], [mk("qwen", "q")], **kw)         # a graded-family record is not the pinned reader
    assert next(iter(sc.values()))["status"] == "pending"
    sc = M.scene_scores([row], [mk("google", "g")], **kw)
    assert next(iter(sc.values()))["status"] == "pending"
    sc = M.scene_scores([row], [mk("deepseek", "deepseek-v4-flash")], **kw)
    u = next(iter(sc.values()))
    assert u["status"] == "ok" and u["mean"] == pytest.approx(0.75)
    assert M.scene_scores([row], [], **kw)["r|CC3-tea-hut|1"]["status"] == "pending"
    sc = M.scene_scores([row], [], reader_for=M.ReaderSpec("q", "qwen"), label="m", cases={}, graded_family="qwen")
    assert "graded model's family" in next(iter(sc.values()))["reason"]


def test_judge_term_halves_exactly():
    from crucibleforge import agree as AG
    d = [0.05, -0.02, 0.1, 0.0, 0.07]
    assert M.judge_term(d, 2) == pytest.approx(AG.judge_sd(d) / 2 ** 0.5)
    assert M.judge_term([]) is None


def test_run_second_reader_store_and_resume(tmp_path, monkeypatch):
    from crucibleforge import agree as AG
    row = {"case_id": "CC3-tea-hut", "rubric": "rp_deep", "bench_run_id": "r", "repeat": 1,
           "judge": {"instrument": {"rubric_sha": "abc"}, "elements": {"results": [{"id": "a", "pass": True}]}},
           "conversation": [{"content": "x"}]}
    calls = []
    verdict = {"elements": {"results": [{"id": "a", "pass": False}]}}
    monkeypatch.setattr(AG, "regrade_long", lambda jc, r, th: (calls.append(th), verdict)[1])
    # Part 1 (2026-11-21): a hosted second reader is refused before any call; the store mechanics stay for a local one
    from crucibleforge.judge45 import HostedGrader
    with pytest.raises(HostedGrader):
        M.run_second_reader([row], M.ReaderSpec("deepseek-v4-flash", "deepseek", hosted=True), jc=None,
                            results=tmp_path, label="m", cases={}, graded_family="qwen")
    assert calls == []
    rd = M.ReaderSpec("some-local-reader", "mistral", hosted=False)
    out = M.run_second_reader([row], rd, jc=None, results=tmp_path, label="m", cases={}, graded_family="qwen")
    assert len(out) == 1 and calls == [True] and out[0]["reader_stamp"]["prompt_sha"] == "abc"
    out = M.run_second_reader([row], rd, jc=None, results=tmp_path, label="m", cases={}, graded_family="qwen")
    assert len(out) == 1 and calls == [True]                      # no second call
    with pytest.raises(M.NoFittingReader):
        M.run_second_reader([row], M.ReaderSpec("q", "qwen"), jc=None, results=tmp_path, label="m", cases={}, graded_family="qwen")
