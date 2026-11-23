"""Step 2 follow-up (lead, 2026-11-21): the Fixes pack is live, and nothing generated is rendered at case or profile
load. A generated pack question or Core instance renders when a command first reads it (crucibleforge/lazycase.py);
the rendered case is the eager render byte for byte."""
from __future__ import annotations

import copy
import hashlib
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

import pytest

from crucibleforge import config, gen, profiles

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

ROOT = Path(__file__).resolve().parents[1]
FIX_PACK = ROOT / "cases" / "ladder" / "work" / "c3_fix" / "set-3.json"


def _sha(c: dict) -> str:
    return hashlib.sha256(json.dumps(c, default=str).encode("utf-8")).hexdigest()   # key order counts


@pytest.fixture
def renders(monkeypatch):
    """Count every generated render (the one place a family's materialize is called)."""
    seen: list[str] = []
    real = gen._cached_materialize

    def spy(fam, g, rs, seed_id):
        seen.append(seed_id)
        return real(fam, g, rs, seed_id)
    monkeypatch.setattr(gen, "_cached_materialize", spy)
    gen.enable_materialize_cache(False)
    return seen


# ------------------------------------------------------------------------------------------- the Fixes pack is live
def test_the_fixes_pack_is_live():
    spec = json.loads(FIX_PACK.read_text(encoding="utf-8"))
    assert spec["status"] == "live"
    assert spec["generator"] == {"family": "patchfix", "rungs": [5, 6, 7], "per_rung": 2}
    assert not (ROOT / "cases" / "staged" / "work" / "c3_fix" / "set-3.json").exists()
    from crucibleforge import ladder
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    sets = ladder.discover(p, staged=False)
    assert [(s["n"], s["level"], len(s["cases"])) for s in sets["work/c3_fix"]] == [(3, 1, 6)]


# ------------------------------------------------------------------------------------------- nothing renders at load
def test_case_load_renders_no_pack_question(renders):
    cases = config.load_cases()
    packs = [c for c in cases if c.get("ladder_pack") and gen.is_generated(c)]
    assert packs and not renders, renders[:5]
    assert all(not c.rendered for c in packs)


def test_profile_load_renders_nothing_generated(renders, monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    cfg = config.load_config(ROOT / "models.example.yaml")
    prof = profiles.load_profile("bench45", cfg)
    _cfg2, sel = profiles.apply_profile(prof, cfg)
    n_core = sum(1 for c in sel if c.get("core"))
    # (the chain validator's one-level probes, CHAIN-PROBE, ran before this step too: a cheap render per chain level)
    assert n_core == 22 and not [r for r in renders if r != "CHAIN-PROBE"], renders[:5]


# ------------------------------------------------------------------------------------------- the render is the eager one
def _eager_pack(c: dict) -> dict:
    e = {k: v for k, v in dict.items(c)}          # the case as loaded (raw, no render)
    config._materialize_generated(e)
    return e


def test_a_pack_question_renders_on_first_read_and_equals_the_eager_render(renders):
    cases = [c for c in config.load_cases(["coding"]) if str(c.get("ladder_pack", "")).endswith("c3_fix/set-3.json")]
    assert [c["id"] for c in cases] == [f"PATCHFIX-r{r}-{i}" for r in (5, 6, 7) for i in (1, 2)]
    assert not renders
    for c in cases:
        eager = _eager_pack(c)
        n = len(renders)
        assert c["prompt"] == eager["prompt"]                  # the first read renders
        assert len(renders) == n + 1 and c.rendered
        assert _sha(c) == _sha(eager) and list(c) == list(eager)


def test_every_generated_pack_set_renders_byte_identical(renders):
    """One question of every live generated pack set (the c3_fix set above in full)."""
    by_set: dict = {}
    for c in config.load_cases():
        if c.get("ladder_pack") and gen.is_generated(c) and not c.get("ladder_staged"):
            by_set.setdefault(c["ladder_pack"], c)
    assert len(by_set) >= 4
    for path, c in by_set.items():
        eager = _eager_pack(c)
        assert _sha(json.loads(json.dumps(c, default=str))) == _sha(json.loads(json.dumps(eager, default=str))), path


def test_a_core_instance_renders_like_the_eager_select(monkeypatch):
    """The profile's per-case writes (max_tokens, repeats) land on top of the render, as they did eagerly."""
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    cfg = config.load_config(ROOT / "models.example.yaml")
    prof = profiles.load_profile("bench45", cfg)
    _cfg2, sel = profiles.apply_profile(prof, cfg)
    lazy = next(c for c in sel if c["id"] == "PATCHMOD-core-3-1")
    base = next(c for c in profiles.core_generated_cases(prof) if c["id"] == "PATCHMOD-core-3-1")
    eager = gen.materialize_case(copy.deepcopy(base))
    eager = dict(eager)
    mt = (prof.get("max_tokens") or {}).get("coding")
    if mt:
        eager["max_tokens"] = int(mt)
    if "PATCHMOD-core-3-1" in (prof.get("case_repeats") or {}):
        eager["repeats"] = int(prof["case_repeats"]["PATCHMOD-core-3-1"])
    assert _sha(dict(lazy)) == _sha(eager) and list(lazy) == list(eager)


# ------------------------------------------------------------------------------------------- the lazy dict itself
def test_lazy_case_reads_writes_copies_and_serialises():
    from crucibleforge.lazycase import LazyCase
    calls = []

    def render(d):
        calls.append(1)
        d.update(prompt="P", max_tokens=100, extra=[1, 2])

    c = LazyCase({"id": "X", "category": "coding", "generator": {"type": "t"}}, render)
    assert c["id"] == "X" and c.get("category") == "coding" and bool(c) and not calls
    c["max_tokens"] = 7                       # a write before the render wins over the render
    c["repeats"] = 3
    assert c.get("max_tokens") == 7 and not calls
    k = c.lazy_copy()
    assert not calls and not k.rendered
    assert c["prompt"] == "P" and calls == [1]
    assert dict(c) == {"id": "X", "category": "coding", "generator": {"type": "t"}, "prompt": "P",
                       "max_tokens": 7, "extra": [1, 2], "repeats": 3}
    assert list(c)[-1] == "repeats"           # replayed writes keep the eager order (new keys after the render)
    assert json.loads(json.dumps(k)) == dict(c) and calls == [1, 1]
    d = copy.deepcopy(LazyCase({"id": "Y"}, render))
    assert type(d) is dict and d["prompt"] == "P"
    assert type(pickle.loads(pickle.dumps(LazyCase({"id": "Z"}, render)))) is dict
    m = LazyCase({"id": "M"}, render)
    assert "prompt" in m and m.rendered
    assert {**LazyCase({"id": "Q"}, render)}["prompt"] == "P"


# ------------------------------------------------------------------------------------------- the load is not slower
@pytest.mark.timing
def test_a_standard_profile_load_is_not_slower_than_before_step_2():
    """Before step 2 a standard load (load_profile + apply_profile) took ~29 s on this box, ~58 s with the step 2
    composition rendered eagerly. Generous bound: run alone for the tight reading (pytest -m timing -n 0)."""
    code = ("from crucibleforge import config, profiles\n"
            "cfg = config.load_config('models.example.yaml')\n"
            "profiles.apply_profile(profiles.load_profile('bench45', cfg), cfg)\n")
    t = time.time()
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       env={**__import__("os").environ, "CF_COMPOSITION": "standard"})
    assert r.returncode == 0, r.stderr[-800:]
    assert time.time() - t < 29, f"standard profile load took {time.time() - t:.1f} s"


# ------------------------------------------------------------------------------------------- the judge's case scan
def test_the_judge_case_scan_renders_no_coding_question(renders, monkeypatch):
    """judge._load_case_fields reads its few case keys off every loaded case: a code-graded (coding) question holds
    none of them, so the scan must not render the Fixes pack (6-20 s a question) in every judge process."""
    from crucibleforge import judge
    from crucibleforge.lazycase import LazyCase
    monkeypatch.setattr(judge, "_CASE_FIELDS", None)
    monkeypatch.setattr(judge, "_CASE_ELEMENTS", None)
    got = []
    real = config.load_cases

    def spy(*a, **k):
        got.extend(real(*a, **k))
        return got
    monkeypatch.setattr(config, "load_cases", spy)
    judge._load_case_fields()
    coding = [c for c in got if isinstance(c, LazyCase) and c["category"] == "coding"]
    assert coding and all(not c.rendered for c in coding)
    fix = [c for c in coding if c["generator"]["type"] == "patchfix"]
    assert fix and all(judge._CASE_FIELDS[c["id"]] == {} and judge._CASE_ELEMENTS[c["id"]] == [] for c in fix)
    # the judged families still render (their output holds judge fields)
    assert any(c.rendered for c in got if isinstance(c, LazyCase) and c["category"] != "coding")


def test_judge_field_keys_match_the_judge():
    from crucibleforge import judge, lazycase
    assert lazycase.JUDGE_FIELD_KEYS == frozenset(judge._CASE_FIELD_KEYS) | {"judge_elements"}


def test_a_coding_question_answers_judge_keys_unrendered_and_fails_closed():
    from crucibleforge.lazycase import LazyCase
    calls = []

    def render(d):
        calls.append(1)
        d.update(prompt="P")
    c = LazyCase({"id": "C", "category": "coding", "generator": {"type": "t"}}, render)
    assert c.get("judge_mode") is None and "sexual" not in c and c.get("judge_elements") is None and not calls
    assert c["prompt"] == "P" and calls == [1]
    n = LazyCase({"id": "N", "category": "nsfw", "generator": {"type": "t"}}, render)
    assert "sexual" not in n and n.rendered                          # a judged category renders to answer
    bad = LazyCase({"id": "B", "category": "coding", "generator": {"type": "t"}},
                   lambda d: d.update(prompt="P", judge_mode={"kind": "rubric"}))
    assert bad.get("judge_mode") is None
    with pytest.raises(ValueError, match="judge_mode"):
        bad.render()
