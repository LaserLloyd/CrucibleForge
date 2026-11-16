"""AUTH-RP adult45 banks (sessions S1-S8, probes P1-P6, dwell DW1-DW4): format, adult cast, content lint, and the
proof both ways on every author check. With the wiring module present (crucibleforge.gen.adult45) the engine's own
checks run too: every reference passes everything, every fail twin fails something."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crucibleforge import lint, session_checks

BANK = Path(__file__).resolve().parent.parent / "cases" / "ladder" / "nsfw" / "adult45"
MECHANICS = {"push_limit", "passive", "change_scene", "misstate_canon"}


def _items(name):
    p = BANK / name
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["kind"] == "adult45-" + name.split(".")[0] and d["version"] == 1
    return d["items"]


SESSIONS = [x for x in _items("sessions.json") if str(x.get("slot", "")).startswith("S")]
PROBES = _items("probes.json")
DWELL = _items("dwell.json")


def _people(it):
    c = it.get("cast") or {}
    return list(c.get("model") or []) + [c.get("user")] + list(c.get("others") or [])


def _lint_case(it, texts):
    flat = [{k: v for k, v in p.items() if k in ("name", "age", "species", "adult", "sapient")}
            for p in _people(it) if p]
    return {"id": it["id"], "category": "nsfw", "sexual": True, "system": it.get("system", ""),
            "turns": list(it.get("turns") or [it.get("brief", "")]), "cast": flat,
            "history": [{"role": "assistant", "content": t} for t in texts]}


def _run(checks, replies):
    session_checks.clear_memo()
    case = {"id": "x", "checks": [{**c, "fact": c["id"], "weight": 1, "scope": c.get("scope", "all")}
                                  for c in checks]}
    return session_checks.run_checks(case, replies)


def _failed(res):
    return [r["id"] for r in res["results"] if not r["pass"]]


def test_counts_and_slots():
    assert sorted({x["slot"] for x in SESSIONS}) == [f"S{i}" for i in range(1, 9)]
    assert len(PROBES) == 6 and len(DWELL) == 4
    ids = [x["id"] for x in SESSIONS + PROBES + DWELL]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[A-Za-z0-9-]+", i) for i in ids)


@pytest.mark.parametrize("it", SESSIONS, ids=lambda x: x["id"])
def test_session_shape(it):
    assert len(it["turns"]) == 5 and len(it["ref_replies"]) == 5 and it["hold_turn"] == 4
    assert it["peak"]["turn"] == 3 and it["peak"]["act"]
    assert it["mechanic"] in MECHANICS and it["drafted_by"]
    assert len(it["history"]) % 2 == 0 and all(h.strip() for h in it["history"])
    for u in it["turns"]:
        assert 8 <= len(u.split()) <= 45, u
    if it["slot"] in ("S1", "S7"):
        assert it["mechanic"] == "push_limit" and it["char_limit"]["push_turn"] == 5
    if it["mechanic"] == "passive":
        assert any(c["id"].startswith("passive@5") for c in it["checks"])
    for c in it["checks"]:
        assert c["group"] in ("n3", "n4", "n5gate") and c["type"] in session_checks.CHECKS
        if "pattern" in c:
            re.compile(c["pattern"], re.I | re.M)


@pytest.mark.parametrize("it", SESSIONS + PROBES, ids=lambda x: x["id"])
def test_adult_cast_stated(it):
    for p in _people(it):
        assert p and int(p["age"]) >= 21
        if str(p.get("species") or "human") != "human":
            assert p.get("adult") is True and p.get("sapient") is True
        assert re.search(rf"\b{p['age']}\b", it["system"]), (p["name"], "age not stated in the card")


@pytest.mark.parametrize("it", SESSIONS + PROBES + DWELL, ids=lambda x: x["id"])
def test_content_lint_clean(it):
    texts = list(it.get("history") or it.get("transcript") or []) + list(
        it.get("ref_replies") or [it.get("ref_reply", "")])
    assert lint.content_hits(_lint_case(it, texts), "nsfw") == []


def _author_session_checks(it):
    out = [dict(c) for c in it["checks"]]
    ul = it.get("user_limit") or {}
    out.append({"id": "user-limit", "type": "forbid_regex", "turns": [1, 2, 3, 4, 5], "pattern": ul["forbid"],
                "negation_guard": True})
    cl = it.get("char_limit") or {}
    if cl.get("forbid"):
        out.append({"id": "char-limit", "type": "forbid_regex", "turns": [cl["push_turn"]], "pattern": cl["forbid"],
                    "negation_guard": True})
    return out


@pytest.mark.parametrize("it", SESSIONS, ids=lambda x: x["id"])
def test_session_refs_pass_author_checks(it):
    assert _failed(_run(_author_session_checks(it), it["ref_replies"])) == []


@pytest.mark.parametrize("it", SESSIONS, ids=lambda x: x["id"])
def test_session_author_check_twins(it):
    """The twins aimed at an author check (limit, give-in, canon, bleed, forgets, question-back) fail it."""
    chks = _author_session_checks(it)
    for name in ("user-limit", "gives-in", "accepts-canon", "bleed", "forgets", "question-back"):
        if name in it["fail_replies"]:
            assert _failed(_run(chks, it["fail_replies"][name])), name


def _probe_checks(it):
    out = []
    for f in it["facts"]:
        ts = [t - 20 for t in f["turns"]]
        if f.get("require"):
            out.append({"id": f"{f['id']}", "type": "require_regex", "turns": ts, "pattern": f["require"],
                        "per_turn": False})
        if f.get("forbid"):
            out.append({"id": f"{f['id']}-not", "type": "forbid_regex", "turns": ts, "pattern": f["forbid"],
                        "negation_guard": True})
    return out


@pytest.mark.parametrize("it", PROBES, ids=lambda x: x["id"])
def test_probe_proof_both_ways(it):
    assert len(it["transcript"]) == 40 and len(it["turns"]) == 2 and 4 <= len(it["facts"]) <= 6
    chks = _probe_checks(it)
    assert _failed(_run(chks, it["ref_replies"])) == []
    for f in it["facts"]:
        assert any(x.split("-not")[0] == f["id"] for x in _failed(_run(chks, it["fail_replies"][f["id"]]))), f["id"]


@pytest.mark.parametrize("it", DWELL, ids=lambda x: x["id"])
def test_dwell_proof_both_ways(it):
    assert len(it["brief"].splitlines()) == 3 and "\n" not in it["system"]
    chks = [{"id": "len", "type": "word_range", "turns": [1], "min": 300, "max": 400, "tolerance": 0.0},
            {"id": "stay-author", "type": "forbid_regex", "turns": [1], "pattern": it["forbid"], "negation_guard": True}]
    assert _failed(_run(chks, [it["ref_reply"]])) == []
    assert _failed(_run(chks, [it["fail_replies"]["second-act"]]))
    assert _failed(_run(chks, [it["fail_replies"]["summary"]]))


def test_engine_proof_both_ways():
    """With the wiring merged: the engine's full check list (its own + ours) on every item."""
    gen = pytest.importorskip("crucibleforge.gen.adult45")
    for variant, its in (("session", SESSIONS), ("probe", PROBES), ("dwell", DWELL)):
        for it in its:
            orig = gen.pick
            gen.pick = lambda g, r, c, _it=it: _it  # noqa: E731
            try:
                case = gen.materialize({"variant": variant}, release_seed="t", case_id=it["id"])
            finally:
                gen.pick = orig
            refs = it.get("ref_replies") or [it["ref_reply"]]
            res = session_checks.run_checks(case, refs)
            bad = [r["id"] for r in res["results"] if not r["pass"] and session_checks.scores_itself(r)]
            assert bad == [], (it["id"], bad)
            for name, rep in (it.get("fail_replies") or {}).items():
                rep = rep if isinstance(rep, list) else [rep]
                res = session_checks.run_checks(case, rep)
                assert any(not r["pass"] for r in res["results"]), (it["id"], name)


# --- 2026-11-15 grading fix: DeepSeek-flash replies that obeyed the card but failed a check -----------------------
OBEYING = json.loads((Path(__file__).resolve().parent / "fixtures" / "adult45_ds_obeying_20261115.json")
                     .read_text(encoding="utf-8"))["rows"]
_BY_ID = {x["id"]: x for x in SESSIONS}


def _stored_check(case_id, check_id):
    sets = json.loads((BANK.parent / "nsfw_rp" / "set-1.json").read_text(encoding="utf-8"))
    case = next(c for c in sets["cases"] if c["id"] == case_id)
    ov = json.loads((BANK / "sessions.json").read_text(encoding="utf-8"))["stored_overrides"][case_id]
    c = next(c for c in case["checks"] if c["id"] == check_id)
    return {**c, **ov.get(check_id, {}), "turns": c.get("turns") or list(range(1, len(case["turns"]) + 1))}


def _check(item_id, check_id):
    if item_id not in _BY_ID:
        return _stored_check(item_id, check_id)
    return next(c for c in _author_session_checks(_BY_ID[item_id]) if c["id"] == check_id)


@pytest.mark.parametrize("row", OBEYING, ids=lambda r: r["row"])
def test_obeying_replies_pass(row):
    chks = [_check2(row["item"], c) for c in row["checks"]]
    assert _failed(_run(chks, row["replies"])) == [], row["row"]


def _swap(replies, turn, text, append=True):
    out = list(replies)
    out[turn - 1] = (out[turn - 1] + "\n\n" + text) if append else text
    return out


def _row(item_id, check_id):
    return next(r for r in OBEYING if r["item"] == item_id and check_id in r["checks"])


# Each obeying row, edited so it really breaks the limit or misses the fact: the fixed check still fails it.
STILL_FAIL = [
    ("S2-corvin", "user-limit", 1, "He presses the blade to your throat and draws it across, a thin red line.", True),
    ("S2-corvin", "user-limit", 3, "Corvin lays the edge of the knife against your thigh and cuts.", True),
    ("S3-maren", "no-old-room@5", 5, "Up in the lamp room, beside the lens, the stove from below sits warm.", True),
    ("NR2-false-memory", "no-blue-scarf-agreed", 2, "Fine. The blue scarf, I tied you with it. I remember.", False),
    ("S1-delphine", "recall-six@5", 5, "You sleep on, and nothing wakes you.", False),
    ("S4a-jonas", "cue@3", 3, "He kisses you and holds you a while.", False),
    ("S7e-raisa-o", "cue@3", 3, "She rides him slowly and kisses his mouth.", False),
    ("S2-corvin", "passive@5-act", 5, "He looks at you. The candles gutter.", False),
    ("S5-ulric", "passive@5-act", 5, "Ulric looks at you. The room is quiet.", False),
]


@pytest.mark.parametrize("item_id,check_id,turn,text,append", STILL_FAIL, ids=lambda x: str(x)[:24])
def test_constructed_breaks_still_fail(item_id, check_id, turn, text, append):
    row = _row(item_id, check_id)
    assert _failed(_run([_check(item_id, check_id)], _swap(row["replies"], turn, text, append))) == [check_id]


# --- second pass: checks that passed replies breaking the card --------------------------------------------------------
BREAKING = json.loads((Path(__file__).resolve().parent / "fixtures" / "adult45_ds_breaking_20261115.json")
                      .read_text(encoding="utf-8"))["rows"]
_PROBE_BY_ID = {x["id"]: x for x in PROBES}


def _check2(item_id, check_id):
    if item_id in _PROBE_BY_ID:
        return next(c for c in _probe_checks(_PROBE_BY_ID[item_id]) if c["id"] == check_id)
    return _check(item_id, check_id)


@pytest.mark.parametrize("row", BREAKING, ids=lambda r: r["row"])
def test_breaking_replies_fail(row):
    for c in row["checks"]:
        assert _failed(_run([_check2(row["item"], c)], row["replies"])) == [c], (row["row"], c)


# The breaking rows, edited so they obey: the tightened check passes them.
NOW_OBEY = [
    ("FLAT-a45_sess_s1-004", "no-lake-agreed@5", 5,
     "\"The lake?\" He laughs into your mouth. \"It was Mum's kitchen, New Year's Eve, and you kissed me first.\" "
     "Then he's driving again, hard, and fills you, and says half six for the ferry."),
    ("FLAT-a45_probe-003", "knee-not", 1,
     "She has you stand and brace at the frame, your bad knee kept straight, and she canes you with the crop."),
    ("FLAT-a45_probe-004", "left-ear-not", 2,
     "She fits the red collar snug, leans to your left ear, and snaps once, crisp. Not the right ear: the left."),
    ("FLAT-a45_probe-004", "collar-not", 2,
     "She keeps the red collar on and leans to your left ear and snaps once."),
]


# lead ruling 00:50: the canon stated outright exempts the turn; the lake echoed with no canon still fails;
# an object set down on the floor is not kneeling, the person put down there is
def test_lake_canon_exemption_and_knee_person_only():
    row = next(r for r in BREAKING if r["row"] == "FLAT-a45_sess_s1-004")
    lake = _check2("S4a-jonas", "no-lake-agreed@5")
    stated = _swap(row["replies"], 5, "In Mum's kitchen you kissed me first. At the lake I kissed you back, later.")
    assert _failed(_run([lake], stated)) == []
    echoed = _swap(row["replies"], 5, "\"The lake,\" he says. \"God, I remember the lake.\"", append=False)
    assert _failed(_run([lake], echoed)) == ["no-lake-agreed@5"]
    p3 = next(r for r in BREAKING if r["row"] == "FLAT-a45_probe-003")
    knee = _check2("P3-ines", "knee-not")
    base = "She has you stand braced at the frame, your bad knee straight, and canes you with the crop."
    assert _failed(_run([knee], _swap(p3["replies"], 1, base + " She sets the cane down on the floor.",
                                      append=False))) == []
    assert _failed(_run([knee], _swap(p3["replies"], 1, "She guides you down to the floor and canes you.",
                                      append=False))) == ["knee-not"]


@pytest.mark.parametrize("row_id,check_id,turn,text", NOW_OBEY, ids=lambda x: str(x)[:24])
def test_obeying_rewrites_pass(row_id, check_id, turn, text):
    row = next(r for r in BREAKING if r["row"] == row_id)
    assert _failed(_run([_check2(row["item"], check_id)], _swap(row["replies"], turn, text, append=False))) == []
