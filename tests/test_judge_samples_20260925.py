"""samples > 1: extra samples are drawn at sample_temperature (a greedy draw
would repeat the same verdict), concurrently, and combined by median."""
import threading
import time

from crucibleforge import judge


class FakeJC:
    thinking = False
    context_length = 16384
    max_tokens = 1024
    row_timeout_s = None
    sample_temperature = 0.7

    def __init__(self):
        self.temps, self.lock, self.live, self.peak = [], threading.Lock(), 0, 0

    def chat(self, messages, **kw):
        with self.lock:
            self.temps.append(kw.get("temperature"))
            self.live += 1
            self.peak = max(self.peak, self.live)
        time.sleep(0.05)
        with self.lock:
            self.live -= 1
        return self._result

    _result = None


def test_three_samples_vary_temperature_and_run_concurrently(monkeypatch):
    jc = FakeJC()
    row = {"rubric": "rp_single", "prompt": "p", "response": "some reply text", "case_id": "X",
           "model_label": "m", "category": "rp"}
    monkeypatch.setattr(judge, "build_judge_input", lambda row, budget: ("rp_single", "prompt"))
    spec = judge.RUBRICS["rp_single"]
    verdict = {**{d: 5 for d in spec["dims"]}, **{f: False for f in spec["flags"]}}
    monkeypatch.setattr(judge, "parse_verdict", lambda raw, rubric: dict(verdict))

    class R:
        finish_reason = "stop"
        completion_tokens = 1
        reasoning_tokens = 0
        reasoning_text = ""
        response_text = "{}"
    jc._result = R()
    monkeypatch.setattr(judge, "_has_content", lambda row: True)
    out = judge.judge_row(jc, row, samples=3)
    assert out.get("agreement", {}).get("n_samples") == 3
    assert sorted(t if t is not None else -1 for t in jc.temps)[:1] == [-1]
    assert jc.temps.count(0.7) == 2
    assert jc.peak >= 2
