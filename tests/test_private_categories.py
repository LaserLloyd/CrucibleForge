"""Private categories (2026-09-28): local-only benchmark components.

One mechanism, tested end to end on a throwaway fixture (the suite itself runs
with CRUCIBLEFORGE_NO_PRIVATE=1, i.e. as a clean clone — see conftest):

- a case file in cases/private/ is a category like any other locally, but is
  outside the public suite revision and carries its own row stamp;
- profiles/<name>.private.yaml is merged into <name>.yaml;
- profiles.private_scope() is the single source of truth for what to hide;
- `report --public` builds the board without private rows/cases/weights and
  refuses (writes nothing) if any private term survives rendering;
- the tracked tree names no private category, and the ignore/scrub rules
  cover every private path.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from crucibleforge import config as cfgmod
from crucibleforge import profiles, report, version
from crucibleforge.judge import RUBRICS
from crucibleforge.templates.board import render_html

REPO = Path(__file__).resolve().parent.parent
CAT = "zz-secret-cat"
IDS = ["ZS1-hidden-one", "ZS2-hidden-two"]
LABEL = "Hush Component"


@pytest.fixture
def private_env(tmp_path, monkeypatch):
    """A private category + overlay in a tmp tree; results in tmp."""
    pdir = tmp_path / "cases-private"
    pdir.mkdir()
    rubric = "nsfw_craft"
    (pdir / f"{CAT}.json").write_text(json.dumps([
        {"id": i, "prompt": f"private prompt {i}", "rubric": rubric, "max_tokens": 100}
        for i in IDS]), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "PRIVATE_ENABLED", True)
    monkeypatch.setattr(cfgmod, "PRIVATE_CASES_DIR", pdir)
    monkeypatch.setattr(cfgmod, "PRIVATE_CATEGORIES", [CAT])
    monkeypatch.setattr(cfgmod, "CATEGORIES", cfgmod.CATEGORIES + [CAT])
    # config-dir profiles: the public bench.yaml + a private overlay
    cdir = tmp_path / "conf"
    (cdir / "profiles").mkdir(parents=True)
    shutil.copy(REPO / "profiles" / "bench.yaml", cdir / "profiles" / "bench.yaml")
    (cdir / "profiles" / "bench.private.yaml").write_text(yaml.safe_dump({
        "private": [CAT], "labels": {CAT: LABEL}, "repeats": {CAT: 1},
        "max_tokens": {CAT: 100}, "cases": {CAT: IDS},
        "scoring": {"chat": {CAT: 10}}}), encoding="utf-8")
    cfg = cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH)
    cfg["_path"] = str(cdir / "models.yaml")
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", results)
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(results))
    monkeypatch.setattr(report, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(report, "_PROFILE_LABELS", {CAT: LABEL})
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    return {"cfg": cfg, "results": results, "rubric": rubric}


def _write_rows(env, label="m-one"):
    cfg = env["cfg"]
    _, cases = profiles.apply_profile(profiles.load_profile("bench", cfg), cfg)
    pub = next(c for c in cases if c["category"] == "coding")
    dims = RUBRICS[env["rubric"]]["dims"]
    rev = version.revision(cfg)
    base = {"profile": "bench", "bench_revision": rev, "bench_run_id": "r1",
            "ts": "2026-09-28T00:00:00+00:00", "repeat": 1, "model_label": label}
    rows = [{**base, "category": "coding", "case_id": pub["id"], "grade": "pass"}]
    for cid in IDS:
        rows.append({**base, "category": CAT, "case_id": cid, "needs_judge": True,
                     "rubric": env["rubric"], "response": "text",
                     "private_revision": version.private_category_hash(CAT),
                     "judge": {"scores": {d: 8 for d in dims}}})
    (env["results"] / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (env["results"] / f"meta_{label}.json").write_text(json.dumps(
        {"profile": "bench", "bench_revision": rev, "model_id": "x/y"}), encoding="utf-8")


# ------------------------------------------------------------------ loading

def test_overlay_merges_into_the_public_profile(private_env):
    prof = profiles.load_profile("bench", private_env["cfg"])
    assert prof["private"] == [CAT]
    assert prof["cases"][CAT] == IDS and "coding" in prof["cases"]
    assert prof["scoring"]["chat"][CAT] == 10 and prof["scoring"]["chat"]["rp"] == 20
    _, cases = profiles.apply_profile(prof, private_env["cfg"])
    assert {c["id"] for c in cases if c["category"] == CAT} == set(IDS)


def test_private_scope_is_the_single_source(private_env):
    s = profiles.private_scope(private_env["cfg"])
    assert s["categories"] == {CAT}
    assert s["case_ids"] == set(IDS)
    assert s["labels"] == {LABEL}
    assert set(s["terms"]) == {CAT, LABEL, *IDS}


def test_private_by_location_even_without_the_list(private_env, tmp_path):
    """Forgetting `private:` in the overlay must not publish the category."""
    ov = Path(private_env["cfg"]["_path"]).parent / "profiles" / "bench.private.yaml"
    data = yaml.safe_load(ov.read_text())
    del data["private"]
    ov.write_text(yaml.safe_dump(data))
    assert CAT in profiles.private_scope(private_env["cfg"])["categories"]


def test_private_cases_do_not_move_the_public_revision(private_env):
    """The suite revision is the same with and without private case files —
    a clean clone and the operator's box agree, and a private edit never
    relabels the public rows stale (the 2026-09-28 board-wide 'partial' bug)."""
    before = version.cases_hash()
    (cfgmod.PRIVATE_CASES_DIR / f"{CAT}.json").write_text("[]")
    assert version.cases_hash() == before


def test_private_rows_carry_their_own_stamp(private_env):
    ok = {"category": CAT, "private_revision": version.private_category_hash(CAT)}
    assert version.private_row_current(ok)
    assert not version.private_row_current({**ok, "private_revision": "00000000"})
    # the private case file changed -> old private rows are stale
    (cfgmod.PRIVATE_CASES_DIR / f"{CAT}.json").write_text(json.dumps(
        [{"id": i, "prompt": "edited", "rubric": "nsfw_craft"} for i in IDS]))
    assert not version.private_row_current(ok)


# ------------------------------------------------------------------ boards

def test_local_board_shows_the_private_component(private_env):
    _write_rows(private_env)
    md = report.generate(write=True)
    res = private_env["results"]
    assert LABEL in md
    assert LABEL in (res / "report.html").read_text()
    s = json.loads((res / "report.json").read_text())["models"]["m-one"]
    assert s["scorecard"]["components"][CAT] == pytest.approx(80.0)


def test_public_board_excludes_every_private_term(private_env):
    _write_rows(private_env)
    report.generate(write=True)                    # the local board exists too
    md = report.generate(write=True, public=True)
    pub = private_env["results"] / "public"
    texts = {p.name: p.read_text() for p in pub.iterdir()}
    assert set(texts) == {"report.md", "failures.md", "report.json", "report.html"}
    for name, text in texts.items():
        for term in (CAT, LABEL, *IDS):
            assert term.lower() not in text.lower(), (name, term)
    # the public Chat score is the scorecard WITHOUT the private component,
    # not the local one with a column hidden
    s = json.loads(texts["report.json"])["models"]["m-one"]
    assert CAT not in s["scorecard"]["weights"]["chat"]
    assert "m-one" in md
    # the local board was not overwritten
    assert LABEL in (private_env["results"] / "report.md").read_text()


def test_public_coverage_does_not_count_private_cases(private_env):
    _write_rows(private_env)
    local = report._expected_case_count(private_env["cfg"], None)
    public = report._expected_case_count({**private_env["cfg"], "_audience": "public"}, None)
    assert local - public == len(IDS)


def test_public_board_fails_closed_on_a_leak(private_env, monkeypatch):
    """If a private term survives rendering (here: a model LABEL that happens
    to contain it), nothing is written."""
    _write_rows(private_env, label=f"leaky-{CAT}")
    with pytest.raises(report.PrivateLeak):
        report.generate(write=True, public=True)
    assert not (private_env["results"] / "public").exists()


def test_render_html_guard_refuses_a_forbidden_term():
    rows = [{"rank": 1, "label": "m", "components": {"RP": 0.5, LABEL: 0.9}}]
    comps = [{"label": "RP", "side": "chat"}, {"label": LABEL, "side": "chat"}]
    page = render_html(rows, components=comps, forbidden=[LABEL])
    assert LABEL not in page                       # the column is dropped
    with pytest.raises(ValueError):
        render_html(rows, subtitle=f"about {CAT}", components=comps, forbidden=[CAT])
    assert LABEL in render_html(rows, components=comps)   # local: as is


# ------------------------------------------------------------ repo hygiene

def _git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(),
                    reason="needs a git checkout")
@pytest.mark.parametrize("path", ["cases/private/x.json", "profiles/private/x.yaml",
                                  "profiles/bench.private.yaml",
                                  "scripts/bench-midnight-batch.sh"])
def test_private_paths_are_git_ignored(path):
    assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, path


@pytest.mark.parametrize("path", ["cases/private/x.json", "profiles/private/x.yaml",
                                  "profiles/bench.private.yaml",
                                  "scripts/bench-nightly-batch.sh"])
def test_scrub_check_refuses_private_paths(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("scrub_check", REPO / "scripts" / "scrub_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert any(rx.search(path) for rx, _ in mod.FORBIDDEN_IF_COMMITTED), path


def test_no_tracked_file_names_a_private_category():
    """Whatever the operator keeps in cases/private/, no tracked file may name
    those categories or their case ids (the profile, the labels, the docs)."""
    pdir = REPO / "cases" / "private"
    if not pdir.is_dir() or shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("no local private cases")
    terms = set()
    for p in pdir.glob("*.json"):
        terms.add(p.stem)
        terms.update(c["id"] for c in json.loads(p.read_text()))
    tracked = _git("ls-files").stdout.split()
    hits = []
    for f in tracked:
        fp = REPO / f
        if not fp.is_file() or fp.suffix in {".png", ".jpg", ".ico"}:
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        hits += [(f, t) for t in terms if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text)]
    assert not hits, hits
