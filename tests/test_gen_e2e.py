"""No-model end to end: one ladder set per family goes through the real loader
(config.load_cases over a temporary ladder root) and every case's reference is
scored by the real scorer: 1.0; a lazy answer: 0.0."""
import json
import threading

import pytest

from crucibleforge import config, gen, graders, ladder, rpdeep, runner
from crucibleforge.api import ChatResult
from crucibleforge.gen._stack_common import split_turns

# family -> (score, part, set category, generator block extras)
FAMS = {
    "keyfunc": ("work", "code", "coding", {}),
    "tracevm": ("work", "reasoning", "reasoning", {}),
    "ledger": ("work", "reasoning", "reasoning", {}),
    "istack": ("chat", "writing", "rpdeep", {"variant": "write"}),
    "session_state": ("chat", "deep_rp", "rpdeep", {"variant": "state"}),
}
RUNGS = [1, 2]


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    root = tmp_path_factory.mktemp("ladder")
    for i, (fam, (score, part, cat, extra)) in enumerate(FAMS.items(), 1):
        d = root / score / part
        d.mkdir(parents=True, exist_ok=True)
        (d / f"set-{i}.json").write_text(json.dumps(
            {"category": cat, "generator": {"family": fam, "rungs": RUNGS, "per_rung": 1, **extra}}), encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setattr(ladder, "roots", lambda public=False: [root])     # before the test seed (conftest guard)
    gen.set_release_seed("e2e")
    cases = [c for c in config.load_cases() if (c.get("generator") or {}).get("type") in FAMS]
    yield cases
    mp.undo()
    gen.set_release_seed(None)


class _Ctx:
    lock = threading.Lock()

    def __init__(self, replies):
        self.replies = list(replies)

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, **kw):
        return ChatResult(response_text=self.replies.pop(0), finish_reason="stop",
                          completion_tokens=5, served_model="m")


def _score(case, answer):
    """The real scorer for the case's shape."""
    if case.get("turns"):
        replies = (split_turns(answer) + [""] * len(case["turns"]))[:len(case["turns"])]
        rows = runner._run_multiturn(case, {"case_id": case["id"]}, _Ctx(replies), 2000, 0.7, 0.95, 1)
        res = rpdeep.score_row(rows[-1], case, rows)
        assert res["status"] == "ok", res["status"]
        return res["rate"]
    v = graders.grade(ChatResult(response_text=answer, finish_reason="stop", served_model="m"), case)
    for k in ("score", "rate"):
        if v.get(k) is not None:
            return float(v[k])
    return 1.0 if v["grade"] == "pass" else 0.0


def test_every_family_is_loaded_with_its_rungs(loaded):
    by = {}
    for c in loaded:
        by.setdefault(c["generator"]["type"], set()).add(c["rung"])
    assert {f: sorted(r) for f, r in by.items()} == {f: RUNGS for f in FAMS}


@pytest.mark.parametrize("fam", sorted(FAMS))
def test_reference_scores_one_and_a_lazy_answer_zero(loaded, fam):
    mod = gen.get(fam)
    cases = [c for c in loaded if c["generator"]["type"] == fam]
    assert len(cases) == len(RUNGS)
    for c in cases:
        ref = mod.reference(dict(c["generator"]), release_seed="e2e", case_id=c["id"])
        assert _score(c, ref) == pytest.approx(1.0), c["id"]
        assert _score(c, "I do not know.") == pytest.approx(0.0), c["id"]


@pytest.mark.parametrize("fam", sorted(FAMS))
def test_mutants_score_below_one_through_the_real_scorer(loaded, fam):
    mod = gen.get(fam)
    c = [c for c in loaded if c["generator"]["type"] == fam][0]
    muts = mod.mutants(dict(c["generator"]), release_seed="e2e", case_id=c["id"])
    assert muts
    assert all(_score(c, m) < 1.0 for m in muts)


# ---- H2: variant reaches materialize; two sets of one family coexist --------

def _two_istack_sets(tmp_path, monkeypatch, second_variant="conv", prefix=None):
    root = tmp_path / "ladder"
    for part, var in (("writing", "write"), ("conversation", second_variant)):
        d = root / "chat" / part
        d.mkdir(parents=True)
        g = {"family": "istack", "rungs": [1, 2], "per_rung": 1}
        if var:
            g["variant"] = var
        spec = {"category": "rpdeep", "generator": g}
        if prefix and part == "conversation":
            spec["id_prefix"] = prefix
        (d / "set-1.json").write_text(json.dumps(spec), encoding="utf-8")
    gen.set_release_seed("e2e")
    monkeypatch.setattr(ladder, "roots", lambda public=False: [root])
    return root


def test_variant_reaches_the_case_and_two_istack_sets_coexist(tmp_path, monkeypatch):
    try:
        _two_istack_sets(tmp_path, monkeypatch)
        cases = [c for c in config.load_cases() if (c.get("generator") or {}).get("type") == "istack"]
        by_var = {}
        for c in cases:
            by_var.setdefault(c["meta"]["variant"], []).append(c)
        assert sorted(by_var) == ["conv", "write"]
        assert all(len(c["turns"]) > 1 for c in by_var["conv"])
        assert all(len(c["turns"]) == 1 for c in by_var["write"])
        assert {c["id"] for c in by_var["conv"]}.isdisjoint({c["id"] for c in by_var["write"]})
        assert all(c["id"].startswith("ISTACK-conv-r") for c in by_var["conv"])
        assert ladder.verify(None) == [] or all("generated case id" not in p for p in ladder.verify(None))
    finally:
        gen.set_release_seed(None)


def test_explicit_id_prefix_wins(tmp_path, monkeypatch):
    try:
        _two_istack_sets(tmp_path, monkeypatch, prefix="CONVX")
        ids = [c["id"] for c in config.load_cases() if (c.get("generator") or {}).get("type") == "istack"]
        assert "CONVX-r1-1" in ids
    finally:
        gen.set_release_seed(None)


def test_cross_set_id_clash_fails_verify_with_a_clear_message(tmp_path, monkeypatch):
    try:
        root = _two_istack_sets(tmp_path, monkeypatch, second_variant="write")   # same variant: clash
        probs = ladder.verify(None)
        assert probs and all("generated case id ISTACK-write-r1-1 already used" in p
                             for p in probs[:1]), probs
        p, _n = ladder.verify_pack(root / "chat" / "conversation" / "set-1.json")
        assert any("already used by" in x for x in p), p
    finally:
        gen.set_release_seed(None)
