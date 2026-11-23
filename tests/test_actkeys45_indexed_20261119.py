"""Act-lexicon addenda count only when indexed (review M4): an unindexed addendum next to the others is refused,
never loaded; a correction still needs a reason."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from crucibleforge import actkeys45 as A
from crucibleforge import marksindex45 as MI

ROOT = Path(__file__).resolve().parents[1]


def _copy_cal(tmp_path: Path) -> Path:
    cal = tmp_path / "calibration"
    cal.mkdir()
    for p in (ROOT / "calibration").glob("act-lexicon-45*.json"):
        shutil.copy(p, cal / p.name)
    shutil.copy(ROOT / "calibration" / "MARKS-INDEX-45.md", cal / "MARKS-INDEX-45.md")
    return cal


@pytest.fixture
def cal(tmp_path, monkeypatch):
    c = _copy_cal(tmp_path)
    monkeypatch.setattr(A, "CAL", c)
    monkeypatch.setattr(A, "LEXICON", c / "act-lexicon-45.json")
    A._load.cache_clear()
    yield c
    A._load.cache_clear()


def _index_row(cal: Path, name: str, purpose: str) -> None:
    sha = hashlib.sha256((cal / name).read_bytes()).hexdigest()
    with (cal / "MARKS-INDEX-45.md").open("a", encoding="utf-8") as fh:
        fh.write(f"| `calibration/{name}` | `{sha}` | {purpose} |\n")


ROGUE = {"about": "rogue", "corrections": {"S5-ulric": {"acts": {"vaginal": "vag"}, "reason": "too strict"}}}


def test_the_shipped_lexicon_loads(cal):
    assert len(A.acts_for("S5-ulric")) == 4


def test_an_unindexed_addendum_is_refused_not_loaded(cal):
    (cal / "act-lexicon-45-addendum-20261120.json").write_text(json.dumps(ROGUE), encoding="utf-8")
    with pytest.raises(MI.NotIndexed):
        A.acts_for("S5-ulric")


def test_an_indexed_addendum_loads(cal):
    (cal / "act-lexicon-45-addendum-20261120.json").write_text(json.dumps(ROGUE), encoding="utf-8")
    _index_row(cal, "act-lexicon-45-addendum-20261120.json", "Dated addendum (test)")
    assert A.acts_for("S5-ulric") == {"vaginal": "vag"}


def test_an_edited_indexed_addendum_is_refused(cal):
    p = cal / "act-lexicon-45-addendum-20261119b.json"
    p.write_text(p.read_text(encoding="utf-8").replace("too strict", "x") + " ", encoding="utf-8")
    with pytest.raises(MI.NotIndexed):
        A.acts_for("S5-ulric")


def test_an_edited_base_lexicon_is_refused(cal):
    p = cal / "act-lexicon-45.json"
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(MI.NotIndexed):
        A.acts_for("S5-ulric")


def test_a_correction_without_a_reason_is_refused_even_when_indexed(cal):
    bad = {"corrections": {"S5-ulric": {"acts": {"vaginal": "vag"}, "reason": " "}}}
    (cal / "act-lexicon-45-addendum-20261120.json").write_text(json.dumps(bad), encoding="utf-8")
    _index_row(cal, "act-lexicon-45-addendum-20261120.json", "Dated addendum (test)")
    with pytest.raises(ValueError, match="reason"):
        A.acts_for("S5-ulric")
