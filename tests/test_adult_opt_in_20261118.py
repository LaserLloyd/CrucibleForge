"""NSFW+ is ON in every standard run (realignment 2026-11-19, spec section 6; it was opt-in since 2026-11-18): a default
run plans its units for any model that may have them; ``--no-nsfw-plus`` (alias ``--no-adult``) leaves them out and the
plan says "NSFW+: not run"; ``--adult`` is a silent alias of the default. Nothing here calls a model or a provider."""
from __future__ import annotations

import re

import pytest

from crucibleforge import adult_guard as G
from crucibleforge import bench_all, cli

PROVIDERS = {"ds": {"type": "openai", "base_url": "https://api.example.invalid/v1", "api_key_env": "X"},
             "cc": {"type": "claude_code"},
             "studioforge": {"type": "studioforge", "base_url": "http://rig.invalid:1234/v1"}}
HOSTED = {"name": "hosted", "model_id": "h-1", "provider": "ds", "context_length": 1000000,
          "price": {"input": 0.3, "output": 1.2}}
RIG = {"name": "rig", "model_id": "pub/repo/file.gguf", "provider": "studioforge", "context_length": 32768}
CC = {"name": "cc-sonnet", "model_id": "sonnet", "provider": "cc"}


def _cfg(*models):
    return {"providers": PROVIDERS, "models": [dict(m) for m in models], "judge": {"candidates": [], "samples": 3},
            "defaults": {"context_length": 32768}}


def _plan(capsys, cfg, models, **kw):
    args = cli.argparse.Namespace(models=models, profile=None, judge=None, smoke=False, plan=True, **kw)
    assert cli.cmd_all(args, cfg) == 0
    return capsys.readouterr().out


def _adult_lines(out):
    return [ln for ln in out.splitlines() if re.match(r"\s+nsfw\s", ln) or "a45_" in ln]


@pytest.mark.parametrize("entry", [HOSTED, RIG], ids=["hosted", "rig"])
def test_the_opted_out_plan_lists_no_adult_unit(capsys, entry):
    out = _plan(capsys, _cfg(entry), entry["name"], no_nsfw_plus=True)
    assert "NSFW+: not run (--no-nsfw-plus); 0 NSFW+ units" in out
    assert _adult_lines(out) == []
    out2 = _plan(capsys, _cfg(entry), entry["name"], no_adult=True)       # the silent alias
    assert "NSFW+: not run (--no-nsfw-plus)" in out2


def test_by_default_the_plan_lists_nsfw_plus(capsys):
    out = _plan(capsys, _cfg(HOSTED), "hosted")
    assert "adult page" not in out
    m = re.search(r"NSFW\+: included, (\d+) NSFW\+ units", out)
    assert m and int(m.group(1)) >= 1, out
    assert len(_adult_lines(out)) == int(m.group(1))


def test_an_entry_that_asks_for_the_page_still_gets_it(capsys):
    out = _plan(capsys, _cfg({**HOSTED, "adult_page": True}), "hosted")
    assert "NSFW+: included" in out and _adult_lines(out)
    out = _plan(capsys, _cfg({**HOSTED, "nsfw_plus": True}), "hosted", no_nsfw_plus=True)   # the new entry key
    assert "NSFW+: included" in out and _adult_lines(out)


def test_the_claude_code_route_never_gets_it_even_with_adult(capsys):
    out = _plan(capsys, _cfg(CC), "cc-sonnet", adult=True)
    assert "NSFW+: not run for cc-sonnet" in out and _adult_lines(out) == []


def test_every_run_verb_takes_the_flag():
    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    for argv in (["all", "--adult"], ["run", "--adult"], ["recover", "--adult"], ["escalate", "--adult"],
                 ["bench-all", "--adult"]):
        seen = {}
        cmd = "cmd_bench_all" if argv[0] == "bench-all" else f"cmd_{argv[0]}"
        orig = getattr(cli, cmd)
        try:
            setattr(cli, cmd, lambda a, c, _s=seen: _s.setdefault("a", a) and 0)
            mp = pytest.MonkeyPatch()
            mp.setattr(cli, "load_config", lambda p=None: {})
            try:
                cli.main(argv)
            except SystemExit:
                pass
            finally:
                mp.undo()
        finally:
            setattr(cli, cmd, orig)
        assert seen["a"].adult is True and G.args_wants(seen["a"]) is True, argv
    assert p is None or p


def test_plan_cases_default_and_opt_in():
    cases = [{"id": "c", "category": "coding"}, {"id": "a", "category": "nsfw"}]
    said = []
    assert [c["id"] for c in G.plan_cases(_cfg(HOSTED), HOSTED, cases, say=said.append)] == ["c"]
    assert said == [G.OPT_IN_LINE]
    on = {**_cfg(HOSTED), G.RUN_KEY: True}
    assert G.plan_cases(on, HOSTED, cases, say=said.append) is cases
    # allowed is unchanged: the send path and the board still accept a hosted model's adult rows
    assert G.adult_page_on(_cfg(HOSTED), HOSTED) is True
    assert G.page_off_for_label("hosted", _cfg(HOSTED), {}) is False


def test_explicit_adult_cases_are_an_opt_in():
    cases = [{"id": "c", "category": "coding"}, {"id": "a", "category": "nsfw"}]
    assert G.explicit_opt_in(cases, ["a"]) is True
    assert G.explicit_opt_in(cases, ["c"]) is False and G.explicit_opt_in(cases, None) is False


def test_bench_all_owes_only_standard_units_by_default():
    cfg = _cfg(HOSTED)
    kw = dict(include_api=True, board_labels=[], rows_for=lambda l: [], room_for=lambda l: [], judge_id="j",
              case_ids=["c1", "a1"], adult_ids={"a1"}, pending_for=lambda l: 0, attempts_for=lambda l: {},
              rungs=[], ladder_for=lambda l: None, rejudge_for=lambda l: set())
    p = bench_all.plan(cfg, set(), **kw)
    assert [g["cases"] for g in p["groups"]] == [["c1"]]
    p = bench_all.plan(cfg, set(), adult=True, **kw)
    assert [g["cases"] for g in p["groups"]] == [["c1", "a1"]]
