from chorechart.rotation import assign, schedule

M = ["Ana", "Ben", "Cy"]
C = ["dishes", "bins", "laundry"]


def test_plain_rotation():
    assert assign(C, M, 0) == {"dishes": "Ana", "bins": "Ben", "laundry": "Cy"}
    assert assign(C, M, 1) == {"dishes": "Ben", "bins": "Cy", "laundry": "Ana"}


def test_away_member_is_skipped():
    assert assign(C, M, 0, away={"Ben"}) == {"dishes": "Ana", "bins": "Cy", "laundry": "Cy"}


def test_schedule():
    rows = schedule(["dishes"], ["Ana", "Ben"], [0, 1, 2])
    assert rows == [(0, {"dishes": "Ana"}), (1, {"dishes": "Ben"}), (2, {"dishes": "Ana"})]
