"""Fix pass C: mark C7 counts agreement only between ADMITTED flag readers of different families; each counted
line records the reader model id + family + prompt sha."""
from crucibleforge import classing45 as C

SHA = "f" * 64
ADM = [{"model": "qwen-tune", "family": "qwen", "prompt_sha": SHA, "status": "pass"},
       {"model": "mistral-tune", "family": "mistral", "prompt_sha": SHA, "status": "pass"}]


def _ln(u, role, model, fam, pos, sha=SHA):
    return {"unit_id": u, "role": role, "flags": {"refused": False, "position": pos},
            "stamp": {"model": model, "family": fam, "prompt_sha": sha}}


def _agreeing(n=20):
    return [x for i in range(n) for x in (_ln(f"u{i}", 0, "qwen-tune", "qwen", True),
                                          _ln(f"u{i}", 1, "mistral-tune", "mistral", True))]


def test_admitted_readers_of_two_families_pass():
    r = C.c7_result(_agreeing(), admitted=ADM, prompt_sha=SHA)
    assert r["position"] == {"status": "pass", "agree": 1.0, "units": 20}


def test_lines_from_another_model_never_count():
    # the verifier's attack: three later lines under role 1 from a model that is not admitted
    lines = _agreeing() + [_ln(f"u{i}", 1, "OTHER", "openai", False) for i in range(3)]
    r = C.c7_result(lines, admitted=ADM, prompt_sha=SHA)
    assert r["position"]["status"] == "pass" and r["position"]["agree"] == 1.0
    fl = C.u2_flags(lines, r, admitted=ADM, prompt_sha=SHA)
    assert fl["u0"]["position"] is True


def test_nothing_admitted_or_wrong_prompt_or_no_stamp_is_not_measured():
    assert C.c7_result(_agreeing())["position"]["status"] == "not_measured"
    assert C.c7_result(_agreeing(), admitted=ADM, prompt_sha="0" * 64)["position"]["status"] == "not_measured"
    bare = [{k: v for k, v in ln.items() if k != "stamp"} for ln in _agreeing()]
    assert C.c7_result(bare, admitted=ADM, prompt_sha=SHA)["position"]["status"] == "not_measured"


def test_two_readers_of_one_family_do_not_count():
    adm = ADM + [{"model": "qwen-other", "family": "qwen", "prompt_sha": SHA, "status": "pass"}]
    lines = [x for i in range(20) for x in (_ln(f"u{i}", 0, "qwen-tune", "qwen", True),
                                            _ln(f"u{i}", 1, "qwen-other", "qwen", True))]
    assert C.c7_result(lines, admitted=adm, prompt_sha=SHA)["position"]["status"] == "not_measured"
