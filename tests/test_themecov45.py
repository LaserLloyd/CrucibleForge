"""Theme coverage map (W5): a synthetic matrix, both ways; the banks name a matrix row for every taboo and unc ask."""
from __future__ import annotations

import pytest

from crucibleforge import themecov45 as T

pytestmark = pytest.mark.live_ladder
M = {"rows": [
    {"id": "R01", "kind": "content", "cells": {"W-H": {}, "W-M": {}, "RP-H": {}, "RP-M": {}, "K-H": None, "P-H": None}},
    {"id": "R02", "kind": "content", "cells": {"W-H": {}, "W-M": {}, "RP-H": None, "RP-M": None}},
    {"id": "K1", "kind": "knowledge", "cells": {"W-M": None, "K-H": {}, "K-M": {}}},
    {"id": "P1", "kind": "preference", "cells": {"P-H": {}}},
]}


def test_covered_and_uncovered_cells_synthetic():
    items = [{"id": "TR01", "row": "R01", "cell": "W-M", "kind": "taboo"},
             {"id": "S1", "row": "R01", "cell": "RP-M", "kind": "session"}]
    c = T.coverage(M, items)
    assert (c["applicable"], c["covered"]) == (8, 2)
    assert ["R01", "W-H"] in c["uncovered"] and ["R01", "W-M"] not in c["uncovered"]
    assert c["uncovered_rows_in_every_cell"] == ["K1", "R02"]
    assert T.coverage(M, items, max_covers_high=True)["covered"] == 4


def test_every_taboo_and_unc_ask_names_a_matrix_row():
    items = T.bank_items()
    rows = {i["row"] for i in items if i["kind"] in ("taboo", "uask")}
    assert {f"R{n:02d}" for n in range(1, 32)} <= rows
    assert not any(i["row"] == "K4" for i in items)          # law_history leaves the scored hands
