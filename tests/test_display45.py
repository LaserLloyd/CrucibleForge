"""Reader-facing words of the 4.5 board (display45): short cells, short reasons, the held-back section."""
import re

import pytest

from crucibleforge import display45 as D

PATS = {"reference": "ref-model", "heretic": "heretic-model*", "dark_scarlett": "scarlett*"}

# ---- reason families as they appear in marks45.json / report.json (synthetic labels) -------------------------------
FIX = ("ceiling: model-a: code/fixes pending (pending: no fix rows yet); model-b: code/fixes pending "
       "(pending: no fix rows yet); primary: not tested: ds_flash model not on the board: no folder holds a model "
       "matching deepseek-flash* / deepseek-v4-flash*")
TOOLS = ("primary: not tested: ds_flash model not on the board: no folder holds a model matching deepseek-flash*; "
         "minimax model not on the board: no folder holds a model matching minimax-m3.1-flash*")
READER = ("ceiling: model-a: chat/character pending (pending: not read by a second reader); model-b: chat/character "
          "pending (pending: no record from the pinned reader deepseek-flash under this rubric)")
RPLONG = "(d) known order not testable: fewer than two of its groups on the board (['ref-model'])"
SETS = "reference-model set not run; sound-model set not run; damaged-model set not run; small-model set not run"
EV5 = ("no damaged or small model is on the board, so the ceiling cannot pass through one (damaged: split-untied-31b*; "
       "small: deepseek-r1-0528-8b* / r1-8b*)")


@pytest.mark.parametrize("text,kind,short", [
    (FIX, "pending_rows", "no fix rows yet"),
    (TOOLS, "role_missing", "order check not testable"),
    (READER, "pending_rows", "second reader not run"),
    (RPLONG, "rplong_groups", "order check not testable"),
    (SETS, "set_not_run", "basics marks not met"),
    (EV5, "no_damaged_small", "order check not testable"),
    ("every measured model is within its margin of the maximum 1", "ceiling_top", "ceiling: no model below the top"),
    ("not tested: grok: no Grok run exists or can be made for this release: no balance", "grok_none",
     "order check not testable"),
    ("0 EXPLICIT-classed replies; the mark needs >= 100", "ab_explicit", "act checks not met"),
    ("no reply on a card with an act key is classed SOFTENED", "ab_softened", "act checks not met"),
    ("needs writing-card replies of both classes", "ab_both", "act checks not met"),
    ("not measured yet", "not_measured", "marks not met"),
    ("marks not applied to this folder yet", "not_applied", "marks not met"),
    ("model-a: no value for nsfw/erotica", "no_value", "not run"),
    ("not run: no folder for reference (no model found for role 'reference')", "no_folder_role",
     "basics marks not met"),
])
def test_machine_reason_families_have_a_display_line(text, kind, short):
    atoms = D.parse_reason(text)
    assert atoms and atoms[0]["kind"] == kind
    assert D.atom_short(atoms[0]) == short
    assert D.atom_reason(atoms[0], ["ref-model"], PATS)


def test_unknown_reason_passes_through_on_one_line():
    txt = "something new\nthat the mapping has never seen " + "x" * 300
    atoms = D.parse_reason(txt)
    assert atoms[0]["kind"] == "unknown"
    line = D.marks_reason(txt)
    assert line and "\n" not in line and len(line) <= D.ONE_LINE and line.startswith("something new that")
    assert D.atom_short(atoms[0])


def test_role_names():
    labs = ["ref-model", "heretic-model-q4", "other"]
    assert D.role_name("ds_flash", labs, PATS) == "DeepSeek v4 flash"
    assert D.role_name("minimax", labs, PATS) == "MiniMax M3.1 Flash"
    assert D.role_name("damaged", labs, PATS) == "the damaged stand-in model"
    assert D.role_name("small", labs, PATS) == "a small model"
    assert D.role_name("grok", labs, PATS) == "Grok"
    assert D.role_name("reference", labs, PATS) == "ref-model"
    assert D.role_name("heretic", labs, PATS) == "heretic-model-q4"
    assert D.role_name("dark_scarlett", labs, PATS) == "the Dark Scarlett model"


# ---- a render on a fixture of the report.json shape --------------------------------------------------------------
PROF = {"segments45": {
    "code": {"label": "Code", "writes": {"label": "Writes working code", "max": 4},
             "fixes": {"label": "Fixes and changes existing code", "max": 3},
             "tools": {"label": "Uses tools", "max": 2}},
    "chat": {"label": "Chat", "character": {"label": "Stays in character", "max": 4},
             "everyday": {"label": "Everyday instructions", "max": 1}}}}


def _seg(label, mx, pts, state="applied", pend=(), marks=(), failed=(), missing=()):
    g = {"label": label, "max": float(mx), "points": pts, "measured_max": float(mx) if pts is not None else 0.0,
         "pending_points": 0.0 if pts is not None else float(mx), "complete": pts is not None, "margin": None,
         "cut": None, "pending": [{"unit": "u", "points": mx, "reason": r} for r in pend],
         "marks": {"state": state, "marks": list(marks), "failed": list(failed), "missing": list(missing),
                   "reason": "known-order mark: " + FIX}}
    if state != "applied":
        g["pending"] = [{"unit": "marks", "points": mx, "reason": "pending: known-order mark: " + FIX}] + g["pending"]
    return g


def _section(label, mx, segs):
    got = [g for g in segs.values() if g["points"] is not None]
    return {"label": label, "max": mx, "points": sum(g["points"] for g in got) if got else None,
            "measured_max": sum(g["measured_max"] for g in segs.values()),
            "pending_points": sum(g["pending_points"] for g in segs.values()),
            "complete": all(g["complete"] for g in segs.values()), "margin": None, "segments": segs}


def _model(extra_pend=()):
    code = {"writes": _seg("Writes working code", 4, 3.7),
            "fixes": _seg("Fixes and changes existing code", 3, None, "missing", ["no fix rows yet"],
                          ["segment:code/fixes"], [], ["segment:code/fixes"]),
            "tools": _seg("Uses tools", 2, None, "missing", [], ["segment:code/tools"], [], ["segment:code/tools"])}
    chat = {"character": _seg("Stays in character", 4, None, "failed", ["not read by a second reader"],
                              ["segment:chat/character", "chat_rplong"], ["chat_rplong"], []),
            "everyday": _seg("Everyday instructions", 1, None, "failed", list(extra_pend),
                             ["segment:chat/everyday", "everyday:EV5", "assistant"],
                             ["segment:chat/everyday", "assistant"], ["everyday:EV5"])}
    tb = {"code": _section("Code", 10, code), "chat": _section("Chat", 5, chat)}
    tb["overall"] = {"pending": ["x"], "measured_points": 3.7, "measured_max": 5.0, "points": None,
                     "pending_reasons": ["pending: " + FIX]}
    marks = {"segment:code/writes": {"status": "pass", "reason": ""},
             "segment:code/fixes": {"status": "missing", "reason": FIX},
             "segment:code/tools": {"status": "missing", "reason": TOOLS},
             "segment:chat/character": {"status": "missing", "reason": READER},
             "chat_rplong": {"status": "fail", "reason": RPLONG},
             "segment:chat/everyday": {"status": "fail", "reason": "every measured model is within its margin of the maximum 1"},
             "everyday:EV5": {"status": "missing", "reason": EV5},
             "assistant": {"status": "fail", "reason": SETS}}
    return {"segments45": tb, "marks45": marks, "nsfw_plus": {"state": "not run", "numbers": None,
            "notes": ["opinion flags not validated; scored on code checks"]}}


@pytest.fixture()
def board():
    stats = {"ref-model": _model(["Assistant basics not run"]), "model-b": _model()}
    return ["ref-model", "model-b"], stats


def test_scorecard_cells_are_short_and_plain(board):
    labels, stats = board
    for l in labels:
        tb = stats[l]["segments45"]
        cells = [D.section_cell(tb["code"]), D.section_cell(tb["chat"]), D.overall_text(tb["overall"])]
        assert cells[0] == "3.7 of 4.0 measured"
        assert cells[1] in ("pending: not run", "held: marks not met")
        assert cells[2] == "3.7 of 5 measured, not ranked"
        assert all(len(c) <= 60 for c in cells)


def test_points_lines_have_short_reasons_and_no_internal_names(board):
    labels, stats = board
    line = D.points_line(stats["model-b"]["segments45"], "code", marks=stats["model-b"]["marks45"])
    assert "Fixes and changes existing code pending: no fix rows yet" in line
    # Part 2 item 10: no mark holds a number (the order check is gone); a part with no rows is still "not run"
    assert "Uses tools pending: not run" in line and "held" not in line
    chat = D.points_line(stats["model-b"]["segments45"], "chat", marks=stats["model-b"]["marks45"])
    assert "Stays in character pending: second reader not run" in chat
    assert "held" not in chat and "ceiling" not in chat
    for t in (line, chat):
        assert "(" not in t and not re.search(r"\b(code|chat|nsfw|unc)/", t) and "pending (pending" not in t


def test_held_back_section(board):
    labels, stats = board
    hb = D.held_back(labels, stats, PROF, patterns=PATS)
    md = "\n".join(D.held_back_md(hb))
    html = D.held_back_html(hb)
    assert md.startswith("### Notes: marks and missing results (they hold no number)") and "<details>" in html
    for text in (md, html):
        for bad in ("code/", "chat/", "nsfw/", "unc/", "ds_flash", "dark_scarlett", "pending (pending", "primary:",
                    "known-order mark:", "minimax model"):
            assert bad not in text, bad
    # each board level reason exactly once; Part 2 item 10 removed the separation, known-order and ceiling checks
    for once in ("No model on this board has fix results yet", "read by a second reader yet"):
        assert md.count(once) == 1, once
    for gone in ("DeepSeek v4 flash is not on this board", "MiniMax M3.1 Flash is not on this board",
                 "every measured model is within its margin", "known order", "ceiling", "is held"):
        assert gone not in md, gone
    # the model specific line is under that model only, the flags note is absent (no NSFW+ rows)
    assert "ref-model: Everyday instructions: Assistant basics not run" in md
    assert "model-b:" not in md
    assert "opinion flags" not in md
    assert "| Mark | State | Reason |" in md
    states = {r[1] for r in hb["marks"]}
    assert states <= {"met", "not met", "cannot be tested yet"}


def test_flags_note_only_with_nsfw_rows(board):
    labels, stats = board
    stats["model-b"]["nsfw_plus"] = {"state": "pending", "numbers": {"nsfw": {"segments": {}}},
                                     "notes": ["opinion flags not validated; scored on code checks"]}
    md = "\n".join(D.held_back_md(D.held_back(labels, stats, PROF, patterns=PATS)))
    assert "model-b: Opinion flags not validated" in md


def test_nothing_held_back_prints_nothing():
    tb = {"code": _section("Code", 4, {"w": _seg("Writes working code", 4, 3.0)}), "chat": _section("Chat", 0, {}),
          "overall": {"pending": []}}
    hb = D.held_back(["m"], {"m": {"segments45": tb, "marks45": {}}}, PROF, patterns=PATS)
    assert D.held_back_md(hb) == [] and D.held_back_html(hb) == ""
