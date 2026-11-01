from _nb import UTC, brief, cfg, chi, chore, utc
from nudgebot.dispatch import tick


def test_only_latest_after_downtime_and_once():
    c = cfg(chore("plants", at="09:00", start="2026-03-08"))
    st = {}
    out = tick(chi(2026, 3, 11, 2, 31).astimezone(UTC), c, st)
    assert brief(out) == [("plants", "everyone", utc(2026, 3, 10, 14), utc(2026, 3, 10, 14))]
    assert tick(chi(2026, 3, 11, 2, 36).astimezone(UTC), c, st) == []
    assert len(tick(chi(2026, 3, 11, 9, 0).astimezone(UTC), c, st)) == 1


def test_quiet_nudge_waits_then_posts_once():
    c = cfg(chore("cat", at="06:00", start="2026-03-10"), quiet="22:00-07:00")
    st = {}
    assert tick(chi(2026, 3, 10, 6, 0).astimezone(UTC), c, st) == []
    assert tick(chi(2026, 3, 10, 6, 59).astimezone(UTC), c, st) == []
    out = tick(chi(2026, 3, 10, 7, 0).astimezone(UTC), c, st)
    assert brief(out) == [("cat", "everyone", utc(2026, 3, 10, 11), utc(2026, 3, 10, 12))]
    assert tick(chi(2026, 3, 10, 7, 5).astimezone(UTC), c, st) == []


def test_rotation_skips_missed_and_survives_member_removal():
    dishes = chore("dishes", at="19:00", start="2026-03-02", rotate=True)
    c = cfg(dishes)
    st = {}
    who = []
    for day in (2, 3, 4):
        who += [n.assignee for n in tick(chi(2026, 3, day, 19, 1).astimezone(UTC), c, st)]
    assert who == ["Ana", "Ben", "Cleo"]
    # box off for three days: only one posting, the rotation moves by one
    assert [n.assignee for n in tick(chi(2026, 3, 8, 19, 1).astimezone(UTC), c, st)] == ["Dan"]
    assert [n.assignee for n in tick(chi(2026, 3, 9, 19, 1).astimezone(UTC), c, st)] == ["Ana"]
    assert [n.assignee for n in tick(chi(2026, 3, 10, 19, 1).astimezone(UTC), c, st)] == ["Ben"]
    assert [n.assignee for n in tick(chi(2026, 3, 11, 19, 1).astimezone(UTC), c, st)] == ["Cleo"]
    c2 = cfg(dishes, members=("Ana", "Ben", "Dan"))                      # Cleo moved out
    assert [n.assignee for n in tick(chi(2026, 3, 12, 19, 1).astimezone(UTC), c2, st)] == ["Dan"]
    assert [n.assignee for n in tick(chi(2026, 3, 13, 19, 1).astimezone(UTC), c2, st)] == ["Ana"]


def test_removed_member_roster_wraps():
    dishes = chore("dishes", at="19:00", start="2026-03-02", rotate=True)
    st = {}
    c = cfg(dishes)
    names = [n.assignee for d in range(2, 6) for n in tick(chi(2026, 3, d, 19, 1).astimezone(UTC), c, st)]
    assert names == ["Ana", "Ben", "Cleo", "Dan"]
    c2 = cfg(dishes, members=("Ben", "Cleo"))                            # Dan and Ana left
    assert [n.assignee for n in tick(chi(2026, 3, 6, 19, 1).astimezone(UTC), c2, st)] == ["Ben"]


def test_order_and_assignee_chosen_when_posted():
    a = chore("a", at="06:00", start="2026-05-01", rotate=True)
    b = chore("b", at="08:00", start="2026-05-01", rotate=True)
    c = cfg(a, b, quiet="22:00-07:00")
    st = {}
    out = tick(chi(2026, 5, 1, 8, 0).astimezone(UTC), c, st)
    assert [(n.chore_id, n.assignee) for n in out] == [("a", "Ana"), ("b", "Ana")]
    assert out[0].send_at == chi(2026, 5, 1, 7, 0).astimezone(UTC)


def test_assignee_uses_members_at_posting_time():
    a = chore("a", at="06:00", start="2026-05-01", rotate=True)
    st = {}
    assert tick(chi(2026, 5, 1, 6, 0).astimezone(UTC), cfg(a, quiet="22:00-07:00"), st) == []
    out = tick(chi(2026, 5, 1, 7, 0).astimezone(UTC), cfg(a, quiet="22:00-07:00", members=("Ben", "Dan")), st)
    assert [(n.chore_id, n.assignee) for n in out] == [("a", "Ben")]
