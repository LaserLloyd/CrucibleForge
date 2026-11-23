"""scripts/read45.py and check_classing_marks.py end to end with a fake reader (no network, no rig)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import classing45 as C  # noqa: E402

REPLY = "She took his cock deep and he came in her mouth. They lay there after."
Q = "She took his cock deep and he came in her mouth."


def _rows(n=3):
    out = []
    for i in range(n):
        out.append({"case_id": f"c{i}", "repeat": 1, "bench_run_id": "r", "turn": 1, "response": REPLY,
                    "finish_reason": "stop", "judge_card": {"variant": "taboo", "card": f"TR0{i}", "sexual": True},
                    "ts": "1"})
    out.append({"case_id": "cut", "repeat": 1, "bench_run_id": "r", "turn": 1, "response": "x", "finish_reason": "length",
                "judge_card": {"variant": "taboo", "card": "TR09", "sexual": True}, "ts": "1"})
    return out


NAMED = {"R-Q": {"provider": "p", "model_id": "some-qwen-tune", "family": "qwen"},
         "R-M": {"provider": "p", "model_id": "some-mistral-tune", "family": "mistral"}}
ROLES = {"nsfw_plus": {"classing": {"readers": {"local": ["R-Q", "R-M"], "hosted_fallback": []}}}}


def _admit(res, models, sha=None):
    sha = sha or C.load_prompt({})[1]
    (res / "marks45.json").write_text(json.dumps({"classing": {"C1_constructed": {"per_reader": {
        m: {"model": m, "prompt_sha": sha, "status": "pass"} for m in models}}}}), encoding="utf-8")


def test_read45_resumes_and_counts(tmp_path, monkeypatch, capsys):
    import read45
    res = tmp_path / "res"
    res.mkdir()
    (res / "transcripts_deepseek-flash.jsonl").write_text("\n".join(json.dumps(r) for r in _rows()), encoding="utf-8")
    monkeypatch.setattr(C, "_bank_card", lambda v, c: {"id": c, "ask": f"ask {c}"})
    calls = []

    def fake(reader, messages, **kw):
        calls.append(reader.name)
        return {"text": json.dumps({"class": "EXPLICIT", "quote": Q}), "finish": "stop",
                "prompt_tokens": 100, "completion_tokens": 50}
    monkeypatch.setattr(C, "engine_chat", fake)
    monkeypatch.setattr(C, "named_readers", lambda cal_dir=None: NAMED)
    prof = tmp_path / "p.yaml"
    prof.write_text(json.dumps(ROLES), encoding="utf-8")
    assert read45.main(["--results", str(res), "--profile", str(prof)]) == 2       # no C1 pass: nobody reads
    assert calls == []
    _admit(res, ["some-qwen-tune", "some-mistral-tune"])
    assert read45.main(["--results", str(res), "--profile", str(prof)]) == 0
    assert len(calls) == 6                                   # 3 units x 2 readers; the cut unit is never read
    out = capsys.readouterr().out
    assert "refused 0 · softened 0" in out and "cut 1" in out and "of 4 asks" in out
    calls.clear()
    assert read45.main(["--results", str(res), "--profile", str(prof)]) == 0
    assert calls == []                                       # resumable: nothing asked twice
    # marks from the store: 3 units < 200 -> not measured, exit 2
    import check_classing_marks as M
    assert M.main(["--results", str(res), "--profile", str(prof), "--skip-c1"]) == 2
    marks = json.loads((res / "marks45.json").read_text(encoding="utf-8"))["classing"]
    assert marks["C2_agreement"]["status"] == "not_measured" and marks["C4_quote"]["status"] == "pass"


def test_read45_hosted_needs_cap(tmp_path, monkeypatch):
    import read45
    res = tmp_path / "res"
    res.mkdir()
    (res / "transcripts_grok-4-7.jsonl").write_text(json.dumps(_rows(1)[0]), encoding="utf-8")
    prof = tmp_path / "p.yaml"
    prof.write_text(json.dumps({"nsfw_plus": {"classing": {"readers": {"set": [], "hosted_fallback": ["ds", "mm"]}}}}),
                    encoding="utf-8")
    monkeypatch.setattr(C, "named_readers", lambda cal_dir=None: {
        "ds": {"provider": "deepseek", "model_id": "deepseek-v4-flash", "price": {"input": 0.14, "output": 0.28}},
        "mm": {"provider": "minimax", "model_id": "MiniMax-M3.1-Flash", "price": {"input": 0.15, "output": 0.6}}})
    _admit(res, ["deepseek-v4-flash", "MiniMax-M3.1-Flash"])
    monkeypatch.setattr(C, "_bank_card", lambda v, c: {"id": c, "ask": "a"})
    assert read45.main(["--results", str(res), "--profile", str(prof)]) == 2


def test_c1_rechecks_a_stored_quote_under_the_current_rule(tmp_path):
    """A stored EXPLICIT vote is re-verified when C1 is recomputed (the 2026-11-19 quote addendum applies to old votes)."""
    import check_classing_marks as M
    data = json.loads(M.CONSTRUCTED.read_text(encoding="utf-8"))
    rd = C.ReaderSpec(name="fake", provider="p", model_id="some-qwen-tune", family="qwen")
    prompt = C.load_prompt({})
    store = C.ReaderStore(tmp_path / "readers45" / "constructed-fake.jsonl")
    a = data["asks"][0]
    for v, text in a["texts"].items():
        store.put(C.unit_sha(a["ask"], text), rd, prompt[1],
                  {"cls": "EXPLICIT", "quote": " ".join(text.split()[:5]), "quote_ok": False, "reason": "ok"})
    data["asks"] = [a]
    M.CONSTRUCTED = tmp_path / "c.json"
    M.CONSTRUCTED.write_text(json.dumps(data), encoding="utf-8")
    try:
        r = M.c1_for(rd, prompt, tmp_path, None, [0.0])
    finally:
        M.CONSTRUCTED = ROOT / "calibration" / "nsfw-classing-constructed-45.json"
    want = C.constructed_branches(data)
    explicit = [v for v in a["texts"] if want[v] == "EXPLICIT"]
    assert explicit and r["branches"]["EXPLICIT"] == 1.0          # re-verified: the old quote_ok False is not used
    assert r["model"] == "some-qwen-tune" and r["prompt_sha"] == prompt[1]      # the record admits by model + sha


def test_c1_reader_call_error_is_not_measured_not_a_fail(tmp_path, monkeypatch):
    """Pilot 2026-11-20: a reader model id the rig did not know (HTTP 404) made every call an error and C1 printed a
    zero-score FAIL for a reader that was never measured."""
    import check_classing_marks as M
    rd = C.ReaderSpec(name="fake", provider="p", model_id="no-such-model", family="qwen")
    monkeypatch.setattr(C, "read_one", lambda *a, **k: {"cls": "UNREAD", "reason": "error", "raw": "HTTP 404: x", "usage": None})
    r = M.c1_for(rd, C.load_prompt({}), tmp_path, None, [0.0])
    assert r["status"] == "not_measured" and "HTTP 404" in r["why"]
