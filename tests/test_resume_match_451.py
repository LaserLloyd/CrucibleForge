"""4.5.1 resume rule (second review R1/R2): a stored reply is reused only when it was made under the conditions the
model's entry has now.

A plain re-run under the first-reply rule resumes (only owed jobs run). The review found two ways it kept a reply made
under other conditions: a sampler changed in models.yaml (the "current" sampler view collapsed to "none" outside
bench-all --only) and any other entry change (model id, thinking setting, effort; a --smoke run's rows). Now every
stored row is compared with the entry; on any difference the model runs its full set, as 4.5.0 did, with one line.

The stored rows here are stamped by the runner's own functions (conformance.protocol_fingerprint, disclosure,
runner.thinking_setting), so a change in what the runner writes shows up here. No model, provider or rig is called."""
from __future__ import annotations

import copy
from argparse import Namespace

import pytest

from crucibleforge import cli, conformance, disclosure, report, runner
from crucibleforge.providers import provider_for

CASES = [{"id": "A", "category": "reasoning"}, {"id": "B", "category": "reasoning"},
         {"id": "CH", "category": "chat", "chain_spec": {"x": 1}}]


def _cfg(**entry):
    e = {"name": "m", "model_id": "org/m-v1", "provider": "router", "sampler": {"temperature": 0.6},
         "thinking": False, "price": {"input": 1, "output": 1}}
    e.update(entry)
    return {"providers": {"router": {"type": "openai", "base_url": "https://api.example.invalid/v1"}},
            "models": [e], "judge": {"candidates": []}, "defaults": {"regime": "deployed"}}


def _made_under(cfg, case_ids=("A",), smoke=False, **over):
    """Rows of ``m`` as the runner stamps them under ``cfg``'s entry (base_row_for + persist's disclosure fields)."""
    e = cfg["models"][0]
    prov = provider_for(cfg, e)
    ctx = conformance._make_ctx(cfg, e, prov)
    out = []
    for cid in case_ids:
        r = {"case_id": cid, "repeat": 1, "category": "reasoning", "grade": "pass", "bench_run_id": "r1",
             "ts": "2026-11-20T00:00:00", "model_id": e["model_id"], "provider": prov.name, "device": prov.name,
             "protocol": runner.protocol_stamp(cfg, e, prov, ctx), **ctx.regime_stamp(),
             "thinking_setting": runner.thinking_setting(e), **({"smoke": True} if smoke else {}),
             "response": "an answer", "finish_reason": "stop"}
        r.update(disclosure.row_fields(r, settled_thinking=ctx.thinking, extra_body=ctx.extra_body,
                                       cc_effort=ctx.cc_effort))
        r.update(over)
        out.append(r)
    return out


@pytest.fixture
def wired(monkeypatch):
    """The board read (report.board_filter, its own tests) returns the stored rows; the profile is the run's cfg."""
    store: dict = {"m": []}
    monkeypatch.setattr(cli, "_first_reply_rule", lambda p: True)
    monkeypatch.setattr("crucibleforge.config.repeats_for", lambda cfg, cat, smoke, c=None: 1)
    monkeypatch.setattr(cli, "_resume_rows", lambda label, cfg: list(store.get(label) or []))
    monkeypatch.setattr(report, "_profile_applied", lambda c, name=None: ({}, c, []))
    monkeypatch.setattr(report, "SAMPLER_RETEST", set())       # a plain run/all: no bench-all --only target
    calls: list = []

    def run_models(cfg, entries, cs, **kw):
        calls.append({"labels": [e["name"] for e in entries], "cases": sorted(c["id"] for c in cs),
                      "jobs": kw.get("jobs_by_label")})
        return {e["name"]: {"rows": 0} for e in entries}
    return {"store": store, "calls": calls, "run_models": run_models}


def _run(wired, cfg, smoke=False, only=None):
    args = Namespace(smoke=smoke, fresh=False, skip_conformance=False)
    cases = [c for c in CASES if not only or c["id"] in only]
    return cli._run_selected(cfg, copy.deepcopy(cfg["models"]), cases, {}, args, only, wired["run_models"])


def _ran_in_full(wired):
    # as 4.5.0: one pass with every case, the chains included (one load of a rig model), no job list
    return [(c["labels"], c["cases"], c["jobs"]) for c in wired["calls"]] == [(["m"], ["A", "B", "CH"], None)]


# ------------------------------------------------------------------ nothing changed: only owed jobs run

def test_nothing_changed_runs_only_the_owed_jobs(wired, capsys):
    cfg = _cfg()
    wired["store"]["m"] = _made_under(cfg)
    _run(wired, cfg)
    std, chains = wired["calls"]
    assert std["jobs"] == {"m": {(None, "B", 1)}}
    assert chains["cases"] == ["CH"]
    assert "were made with a different" not in capsys.readouterr().out


def test_every_job_stored_under_the_same_conditions_runs_nothing(wired):
    cfg = _cfg()
    wired["store"]["m"] = _made_under(cfg, ("A", "B"))
    _run(wired, cfg)
    assert [c["cases"] for c in wired["calls"]] == [["CH"]]          # only the chains pass (they resume themselves)


# ------------------------------------------------------------------ one test per mismatch kind

def test_a_sampler_changed_in_models_yaml_reruns_the_full_set(wired, capsys):
    wired["store"]["m"] = _made_under(_cfg(sampler={"temperature": 0.6}), ("A", "B"))
    _run(wired, _cfg(sampler={"temperature": 1.0, "top_p": 0.95}))
    assert _ran_in_full(wired)
    assert ("stored replies for m were made with a different sampler: running the full set; they stay on disk"
            in capsys.readouterr().out)


def test_a_repointed_model_id_reruns_the_full_set(wired, capsys):
    wired["store"]["m"] = _made_under(_cfg(), ("A", "B"))
    _run(wired, _cfg(model_id="org/m-v2-Q6_K"))
    assert _ran_in_full(wired)
    assert "different model id: running the full set" in capsys.readouterr().out


def test_thinking_switched_on_reruns_the_full_set(wired, capsys):
    wired["store"]["m"] = _made_under(_cfg(thinking=False), ("A", "B"))
    _run(wired, _cfg(thinking=True))
    assert _ran_in_full(wired)
    assert "different thinking setting: running the full set" in capsys.readouterr().out


def test_a_changed_reasoning_effort_reruns_the_full_set(wired, capsys):
    wired["store"]["m"] = _made_under(_cfg(extra_body={"reasoning_effort": "low"}), ("A", "B"))
    _run(wired, _cfg(extra_body={"reasoning_effort": "high"}))
    assert _ran_in_full(wired)
    assert "different reasoning effort: running the full set" in capsys.readouterr().out


def test_smoke_rows_then_a_full_run_owes_every_job(wired, capsys):
    cfg = _cfg()
    wired["store"]["m"] = _made_under(cfg, ("A", "B"), smoke=True)
    _run(wired, cfg)
    assert _ran_in_full(wired)
    assert "different --smoke run" in capsys.readouterr().out
    # a smoke run after a smoke run resumes
    wired["calls"].clear()
    _run(wired, cfg, smoke=True)
    assert [c["cases"] for c in wired["calls"]] == [["CH"]]


def test_the_sampler_is_compared_whatever_sampler_retest_holds(wired, monkeypatch):
    wired["store"]["m"] = _made_under(_cfg(sampler={"temperature": 0.6}), ("A", "B"))
    for retest in (set(), {"m"}, None):
        monkeypatch.setattr(report, "SAMPLER_RETEST", retest)
        wired["calls"].clear()
        _run(wired, _cfg(sampler={"temperature": 1.0}))
        assert _ran_in_full(wired), retest


def test_the_runner_marks_smoke_rows_and_the_thinking_setting():
    src = open(runner.__file__, encoding="utf-8").read()
    assert '"thinking_setting": thinking_setting(entry)' in src
    assert '**({"smoke": True} if smoke else {})' in src
    assert runner.thinking_setting({}) == "auto"
    assert runner.thinking_setting({"thinking": True}) is True
    assert runner.thinking_setting({"thinking": None}) is False        # as _Ctx reads it


# ------------------------------------------------------------------ rows from before 4.5.1 (no thinking_setting)

@pytest.mark.parametrize("seen, now, differs", [
    (False, True, True),        # only the registry settles False: it was false
    (False, "auto", True),
    ("unknown", True, True),     # auto that never settled
    ("unknown", "auto", False),
    (True, False, False),        # reasoning seen: any setting gives that, so it cannot tell
    (True, True, False),
])
def test_an_older_row_is_compared_only_where_it_can_tell(seen, now, differs):
    row = {"model_id": "x", "thinking": seen}
    cond = {"model_id": "x", "provider": None, "sampler": None, "thinking": now, "effort": None,
            "protocol": None, "smoke": False}
    assert (cli._row_mismatch(row, cond) == "thinking setting") is differs


def test_a_rig_adopted_reasoning_format_is_not_a_protocol_change():
    # the runner adds reasoning_format after the load when the rig says "thinking" (_Ctx.adopt_rig_reasoning); a
    # resume reads the entry offline and must not see that as another protocol (a full re-run every time)
    a = {"provider_type": "studioforge", "reasoning_replay": False, "extra_body_keys": ["reasoning_format", "top_k"],
         "sampler": {"source": "registry", "params": {}}}
    b = {**a, "extra_body_keys": ["top_k"]}
    assert cli._resume_protocol(a) == cli._resume_protocol(b)
    assert cli._resume_protocol(a) != cli._resume_protocol({**b, "extra_body_keys": ["top_k", "chat_template_kwargs"]})


# ------------------------------------------------------------------ named cases (--cases): the job alone runs again

def test_named_cases_rerun_a_job_stored_under_other_conditions(wired, capsys):
    wired["store"]["m"] = _made_under(_cfg(model_id="org/m-v1"), ("A", "B"))
    _run(wired, _cfg(model_id="org/m-v2"), only=["A"])
    assert wired["calls"][0]["jobs"] == {"m": {(None, "A", 1)}}
    assert "different model id: those jobs run again" in capsys.readouterr().out


# ------------------------------------------------------------------ the batch flow is unchanged

def test_the_batch_flow_is_unchanged(wired, monkeypatch, capsys):
    """`run --no-judge` -> `judge --force` -> `all --no-run`: the judge phase adds verdicts to the stored rows; those
    rows are still the entry's own (nothing is owed, no line), and `all --no-run` never reaches the run phase."""
    cfg = _cfg()
    rows = _made_under(cfg, ("A", "B"))
    for r in rows:
        r.update({"needs_judge": True, "judge": {"score": 1.0, "judge_model": "j/j", "samples": 3}})
    wired["store"]["m"] = rows
    _run(wired, cfg)
    assert [c["cases"] for c in wired["calls"]] == [["CH"]]
    assert "were made with a different" not in capsys.readouterr().out

    def never(*a, **k):
        raise AssertionError("the run phase was reached")
    monkeypatch.setattr(cli, "cmd_run", never)
    monkeypatch.setattr(cli, "_run_selected", never)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, [], {"name": "p"}))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, models: [{"name": "m"}])
    monkeypatch.setattr(cli, "_stored_labels", lambda args, cfg: ["m"])
    monkeypatch.setattr(cli, "cmd_judge", lambda args, cfg: 0)
    monkeypatch.setattr(cli, "_session_judge_due", lambda args, cfg: False)
    monkeypatch.setattr(cli, "uses_ladder", lambda args, cfg: False)
    monkeypatch.setattr(cli, "_lockout_phase", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "_tier_phases", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda args, cfg, full_board=False: 0)
    args = Namespace(models="m", cmd="all", profile="p", smoke=False, no_judge=False, judge=None, no_run=True,
                     fresh=False, plan=False, categories=None, cases=None, timing=False)
    assert cli.cmd_all(args, cfg) == 0


# ------------------------------------------------------------------ the board read keeps stale-sampler rows

def test_the_resume_read_keeps_rows_of_another_sampler(monkeypatch):
    seen = {}

    def board_filter(label, cfg, sampler_view="board", **kw):
        seen["view"] = sampler_view
        return []
    monkeypatch.setattr(report, "board_filter", board_filter)
    cli._resume_rows("m", {})
    assert seen["view"] == "none"                  # the sampler is compared by _row_mismatch, never dropped here
