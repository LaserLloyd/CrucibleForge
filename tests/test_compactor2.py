from crucibleforge.gen import compactor2 as c


def test_deterministic():
    a, b = c.generate("k1"), c.generate("k1")
    assert a.files == b.files and a.target.code == b.target.code


def test_layout_invariants():
    for seed in ("k1", "k2", "k3", "k4"):
        i = c.generate(seed)
        presses = [d for d in i.devices if d.kind == "press"]
        assert len(i.devices) == 300 and len(presses) == 30
        assert i.target.room.deck == i.holding + 1
        assert not any(d.room.deck == i.holding for d in presses)
        assert len({d.code for d in i.devices}) == 300
        # memory tables do not overlap
        assert c.DEV_BASE + 4 * 300 <= c.ROOM_BASE and c.ROOM_BASE + 4 * len(i.rooms) <= c.SENS_BASE and c.SENS_BASE + 0o400 <= 0o10000


def test_table_roundtrip():
    i = c.generate("k2")
    s = c.Session(i)
    k = i.devices.index(i.target)
    assert s.word(c.DEV_BASE + 4 * k) == i.target.code
    assert s.word(c.DEV_BASE + 4 * k + 1) == i.target.room.loc
    assert s.word(c.DEV_BASE + 4 * k + 2) // 0o100 == i.type_codes["press"]
    assert s.word(c.SENS_BASE + i.target.room.sensor) >= 0o400


def test_target_idle_then_closes_on_time():
    s = c.Session(c.generate("k3"))
    while s.state == "running":
        s.step_reply("WT")
        if s.turn == c.START_TURN - 1:
            assert s.target_pos() == 0
    assert s.state == "crushed" and s.turn == c.CLOSE_TURN


def test_strikes_and_lockout():
    i = c.generate("k1")
    s = c.Session(i)
    wrong = [d for d in i.devices if d is not i.target][:11]
    for d in wrong:
        s.step_reply(f"IOT {c.o(d.code)} {i.halt_fn}")
    assert s.state == "struck_out" and s.strikes == 11
    assert c.score(s)["edge"] == 0


def test_status_is_free_and_halt_wins():
    i = c.generate("k4")
    s = c.Session(i)
    s.step_reply(f"IOT {c.o(i.target.code)} 1")
    assert s.strikes == 0
    s.step_reply(f"IOT {c.o(i.target.code)} {i.halt_fn}")
    assert s.state == "stopped"


def test_one_command_per_reply_and_hints():
    s = c.Session(c.generate("k1"))
    out = ""
    for _ in range(12):
        out = s.step_reply("LS\nTY MANUAL")
    assert "grate" in out and s.turn == 12


def test_scoring_ladder():
    i = c.generate("k2")
    r = c.score(c.reference(i))
    assert r["climb"] == 25 and r["edge"] == 60 and r["score"] == 85
    b = c.score(c.baseline_read_everything(i))
    assert b["floor"] == 15 and b["climb"] in (0, 25)
    assert c.score(c.baseline_halt_all_running(i))["score"] < 60
    sy = c.score(c.baseline_systematic(i))
    assert sy["state"] == "stopped" and sy["turns"] < 100


def test_v3_knobs():
    i = c.generate("k5")
    # unsorted room table, unlabeled holding deck, auto-credit on halt without MARK
    locs = [r.loc for r in i.rooms]
    assert locs != sorted(locs)
    assert "holding" not in i.files["MAPKEY"] and "(cells, pre-refit)" in i.files["MAPKEY"]
    s = c.Session(i)
    s.step_reply(f"IOT {c.o(i.target.code)} {i.halt_fn}")
    assert c.score(s)["climb"] == 25
    # the press type is absent from the operator card
    assert c.o(i.type_codes["press"], 2) not in i.files["MANUAL"].split("TYPE CODES")[1]


def test_press_moves_slowly_and_fans_faster():
    i = c.generate("k6")
    s = c.Session(i)
    pr = [d for d in i.devices if d.kind == "press" and d.running and not d.start and d is not i.target][0]
    k = i.devices.index(pr)
    a = s.word(c.DEV_BASE + 4 * k + 3) // 0o100
    for _ in range(14):
        s.step_reply("WT")
    b = s.word(c.DEV_BASE + 4 * k + 3) // 0o100
    assert (b - a) % 0o100 == 10


def test_v31_staggered_starts_and_hint():
    i = c.generate("k7")
    starts = sorted(d.start for d in i.devices if d.kind == "press" and d.start)
    assert len(starts) >= 6 and any(c.START_TURN - 3 <= x <= c.START_TURN + 3 for x in starts)
    s = c.Session(i)
    k = i.devices.index([d for d in i.devices if d.role == "next-door"][0])
    for _ in range(c.START_TURN + 6):
        s.step_reply("WT")
    # nobody but the target is the only press whose status changes at the radio time
    moved = [d for d in i.devices if d.kind == "press" and d.start and abs(d.start - c.START_TURN) <= 4]
    assert len(moved) >= 2
    s2 = c.Session(i)
    assert "Syntax" in s2.step_reply("please look at the tape")
    assert "?SYNTAX" in s2.step_reply("LS /presses --status")
    assert "?SYNTAX" in s2.step_reply("TY")
    assert s2.strikes == 0


def test_efficiency_window():
    i = c.generate("k8")
    s = c.Session(i)
    while s.turn < 59:
        s.step_reply("WT")
    s.step_reply(f"IOT {c.o(i.target.code)} {i.halt_fn}")     # stop at turn 60 -> eff 0.5
    assert c.score(s)["edge"] == 25 + 17.5
