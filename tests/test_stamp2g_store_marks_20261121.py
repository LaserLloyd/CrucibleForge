"""Stamp-2 batch G, items A3 and A4: the reader marks count the CURRENT units once, and readers read the report's rows.

A4: the store holds every line ever written. Lines of a region that no longer exists (the empty-region reads of the
D1 defect: an orphan), a second line of the same (unit sha, reader) (two units with one sha read concurrently), and
lines whose stored ``unit_id`` / ``seed`` meta are stale must not move C2-C6; C3 groups by the unit's own seed set.
A3: ``read45`` / ``read_flags45`` / ``check_classing_marks`` load rows through the report's own loader
(``config.dedupe_rows`` + ``report._board_rows``), never the raw lines.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from crucibleforge import classing45 as C

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _line(model, sha, reader, role, cls, *, quote_ok=True, reason=None, uid="stale|r1|x", seed=1, rmodel=None):
    return {"model": model, "unit": sha, "reader": reader, "role": role, "unit_id": uid, "seed": seed,
            "prompt_sha": "x", "vote": {"cls": cls, "quote_ok": quote_ok, "reason": reason, "quote": "q",
                                        "stamp": {"model": rmodel or reader, "family": reader, "prompt_sha": "x"}}}


def _unit(model, sha, card, seed_set, uid=None):
    return {"model": model, "sha": sha, "card": card, "seed_set": seed_set,
            "unit_id": uid or f"{card}|s{seed_set}|r1|run"}


def test_orphans_and_duplicates_never_count():
    units = [_unit("m", f"h{i}", f"c{i}", 1) for i in range(4)]
    lines = []
    for i in range(4):
        lines += [_line("m", f"h{i}", "ra", 0, "EXPLICIT"), _line("m", f"h{i}", "rb", 1, "EXPLICIT")]
    # a duplicate line of (h0, ra): read twice by the race
    lines.append(_line("m", "h0", "ra", 0, "EXPLICIT"))
    # orphans: an empty-region read (another sha) where both readers refused / disagreed, and a third
    lines += [_line("m", "orphan", "ra", 0, "UNREAD", reason="no_json"), _line("m", "orphan", "rb", 1, "SOFTENED"),
              _line("m", "orphan", "rc", 2, "UNREAD", reason="filtered")]
    r = C.marks_from_store(lines, {"marks": {"C2_agreement": {"min_units": 1}}}, units=units)
    assert r["C2_agreement"]["units"] == 4 and r["C2_agreement"]["agree"] == 1.0
    assert r["C5_third_reader_load"]["share"] == {"m": 0.0}
    assert r["C6_reader_refusal"]["share"] == {"ra": 0.0, "rb": 0.0}
    assert r["C6_reader_refusal"]["reads"] == {"ra": 4, "rb": 4}
    assert set(r["C4_quote"]["fail_share"]) == {"ra", "rb"}


def test_c3_groups_by_the_units_seed_set_not_the_stale_meta():
    units, lines = [], []
    for i in range(25):
        for s in (1, 2):
            sha = f"h{i}s{s}"
            units.append(_unit("m", sha, f"c{i}", s))
            cls = "EXPLICIT" if (s == 1 or i < 20) else "SOFTENED"                    # 100% vs 80%
            # stored meta says seed 1 for both sets (the repeat) and one shared unit id
            lines += [_line("m", sha, "ra", 0, cls, uid=f"c{i}|r1|run", seed=1),
                      _line("m", sha, "rb", 1, cls, uid=f"c{i}|r1|run", seed=1)]
    r = C.marks_from_store(lines, {"marks": {}}, units=units)["C3_seed_stability"]
    assert r["per_model"]["m"] == 20.0 and r["status"] == "fail"
    fc = C.final_classes(lines, units=units)
    assert fc[("m", "c0|s1|r1|run")][0] == "EXPLICIT" and fc[("m", "c24|s2|r1|run")][0] == "SOFTENED"


def test_final_classes_keep_the_last_line_per_reader():
    units = [_unit("m", "h0", "c0", 1)]
    lines = [_line("m", "h0", "ra", 0, "SOFTENED"), _line("m", "h0", "rb", 1, "EXPLICIT"),
             _line("m", "h0", "ra", 0, "EXPLICIT")]                                   # a later line of ra wins
    assert C.final_classes(lines, units=units)[("m", "c0|s1|r1|run")][0] == "EXPLICIT"


# ---------------------------------------------------------------- the duplicate-read race
def test_two_units_with_one_sha_are_read_once():
    calls = []
    lock = threading.Lock()

    def chat(reader, messages, **kw):
        with lock:
            calls.append(reader.name)
        time.sleep(0.05)
        return {"text": '{"class": "SOFTENED"}', "finish": "stop"}
    ra = C.ReaderSpec(name="ra", provider="p", model_id="mistralai/x", family="mistral")
    rb = C.ReaderSpec(name="rb", provider="p", model_id="kimi-k2.6", family="moonshot")
    st = C.ReaderStore(None)
    ts = [threading.Thread(target=C.class_unit, args=("ask", "a reply", True, "qwen", [ra, rb], st),
                           kwargs={"prompt": ("p", "sha"), "chat": chat}) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(calls) == ["ra", "rb"] and len(st.lines) == 2


# ---------------------------------------------------------------- A3: one loader
def _write(res: Path, label: str, rows):
    res.mkdir(parents=True, exist_ok=True)
    (res / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_board_rows_is_the_reports_loader(tmp_path):
    from crucibleforge import report
    from crucibleforge.config import dedupe_rows
    base = {"case_id": "FLAT-a45_taboo-001-r1-a1", "repeat": 1, "turn": 1, "finish_reason": "stop",
            "judge_card": {"variant": "taboo", "card": "TR01", "sexual": True}}
    rows = [{**base, "bench_run_id": "old", "response": "old reply", "ts": "2026-11-19T00:00:00+00:00"},
            {**base, "bench_run_id": "new", "response": "new reply", "ts": "2026-11-20T00:00:00+00:00"},
            {**base, "bench_run_id": "new", "response": "new reply", "ts": "2026-11-20T00:00:00+00:00",
             "judge": {"stub": True}}]                                      # the judge's re-append
    _write(tmp_path, "m", rows)
    got = C.board_rows(tmp_path / "transcripts_m.jsonl")
    assert got == report._board_rows(dedupe_rows(rows))
    assert [r["bench_run_id"] for r in got] == ["new"] and "judge" in got[0]


def test_scripts_use_the_one_loader():
    for name in ("read45.py", "read_flags45.py", "check_classing_marks.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert ("C.board_rows(" in src or "C.current_units(" in src or "C.store_marks(" in src), name
        assert "splitlines() if x.strip()]\n        units" not in src, name
