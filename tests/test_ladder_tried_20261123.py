"""A pack job runs at most once per `all`.

2026-11-23: a pack row that ended as an errored row (cut by the context window) was
queued again in every later round of the same invocation, about 20 minutes a round,
up to MAX_LADDER_ROUNDS. ``ladder_todo`` builds jobs as ``(None, case_id, repeat)``
and the round loop stores those in ``tried``, but the membership test looked for
``(case_id, repeat)``, so nothing was ever found there.
"""
import crucibleforge.cli as cli
import crucibleforge.config as config
import crucibleforge.ladder as ladder
import crucibleforge.report as report


def _wire(monkeypatch, have=()):
    monkeypatch.setattr(ladder, "due", lambda *a, **k: {1})
    monkeypatch.setattr(ladder, "states", lambda *a, **k: {})
    monkeypatch.setattr(ladder, "rung_stop", lambda prof: None)
    monkeypatch.setattr(ladder, "set_cases", lambda prof, key, n, **k: [
        {"id": "PK-1", "category": "work"}, {"id": "PK-2", "category": "work"}])
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, **k: [])
    monkeypatch.setattr(cli, "done_jobs", lambda rows: set(have))
    monkeypatch.setattr(config, "repeats_for", lambda cfg, cat, smoke, case=None: 1)
    sets = {"work/x": [{"n": 1}]}
    progress = {"work/x": {"std": 1.0, "sets": {}}}
    return sets, progress


def test_a_job_this_invocation_already_ran_is_not_due_again(monkeypatch):
    sets, progress = _wire(monkeypatch)
    _, jobs, names = cli.ladder_todo("m", {}, {}, sets, 0.9, progress=progress)
    assert jobs == {(None, "PK-1", 1), (None, "PK-2", 1)}
    assert names == ["work/x#1"]
    # the round loop does ``tried |= jobs``; the errored row is still not "done"
    _, again, _ = cli.ladder_todo("m", {}, {}, sets, 0.9, progress=progress, tried=set(jobs))
    assert again == set()


def test_only_the_tried_job_is_left_out(monkeypatch):
    sets, progress = _wire(monkeypatch)
    _, jobs, _ = cli.ladder_todo("m", {}, {}, sets, 0.9, progress=progress,
                                 tried={(None, "PK-1", 1)})
    assert jobs == {(None, "PK-2", 1)}


def test_a_stored_usable_row_is_still_left_out(monkeypatch):
    sets, progress = _wire(monkeypatch, have={("PK-2", 1)})
    _, jobs, _ = cli.ladder_todo("m", {}, {}, sets, 0.9, progress=progress)
    assert jobs == {(None, "PK-1", 1)}
