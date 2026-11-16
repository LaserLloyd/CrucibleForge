"""G-Headroom (scripts/pilot_bars.py) on synthetic pilot rows: each bar flips alone."""
import copy
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pilot_bars.py"
sys.path.insert(0, str(SCRIPT.parent))
import pilot_bars  # noqa: E402

M = ["haiku", "sonnet", "fable"]
FAMS = ["keyfunc", "tracevm", "ledger", "istack", "session"]


def good():
    fams = {}
    for f in FAMS:
        fams[f] = {"sfw": True, "core_level": 3, "top_level": 5,
                   "shares": {"haiku": {"3": 0.30, "5": 0.05},
                              "sonnet": {"3": 0.55, "5": 0.20},
                              "fable": {"3": 0.70, "5": 0.35}}}
    return {"order": M,
            "scores": {"haiku": {"code": 5.0, "chat": 5.5}, "sonnet": {"code": 6.5, "chat": 6.8},
                       "fable": {"code": 7.9, "chat": 8.2}},
            "frontier": {"haiku": {"F1": 0.05}, "sonnet": {"F1": 0.2}, "fable": {"F1": 0.5},
                         "local1": {"F1": 0.1, "F2": 0.2}},
            "families": fams, "noise": {"measured": 0.10, "assumed": 0.10}, "local": ["local1"]}


def bars(d):
    r = pilot_bars.evaluate(d)
    return {k: v["ok"] for k, v in r["bars"].items()}, r


def test_good_passes_and_reports_local():
    b, r = bars(good())
    assert all(b.values()) and r["ok"]
    assert r["reported"] == {"top_local": "local1", "mean_frontier_share": 0.15}


def flip(mut, bar):
    d = good()
    mut(d)
    b, r = bars(d)
    assert b[bar] is False and not r["ok"]
    assert all(v for k, v in b.items() if k != bar), b      # only this bar moved


def test_bar1_best_score_at_ceiling():
    flip(lambda d: d["scores"]["fable"].__setitem__("code", 8.5), "1")


def test_bar2_unlock():
    flip(lambda d: d["frontier"]["fable"].__setitem__("F1", 0.9), "2")


def test_bar3_best_share_too_high_and_too_low_and_flat():
    flip(lambda d: d["families"]["ledger"]["shares"]["fable"].__setitem__("3", 0.86), "3")
    d = good()
    for m in M:                                           # everyone 0.40: best below the band
        d["families"]["ledger"]["shares"][m]["3"] = 0.40
    assert bars(d)[0]["3"] is False
    d = good()                                            # best 0.50 but sd tiny
    for m in M:
        d["families"]["ledger"]["shares"][m]["3"] = 0.50
    assert bars(d)[0]["3"] is False


def test_bar4_order_needs_four_of_five_and_allows_ties():
    d = good()
    d["families"]["keyfunc"]["shares"]["haiku"]["3"] = 0.60   # haiku > sonnet: one family breaks
    d["families"]["keyfunc"]["shares"]["sonnet"]["3"] = 0.55
    b, _ = bars(d)
    assert b["4"] is True                                     # 4 of 5 still passes
    d["families"]["tracevm"]["shares"]["haiku"]["3"] = 0.60
    d["families"]["tracevm"]["shares"]["sonnet"]["3"] = 0.55
    assert bars(d)[0]["4"] is False                           # 3 of 5
    d = good()
    for f in FAMS:                                            # exact ties are allowed
        d["families"][f]["shares"]["sonnet"]["3"] = d["families"][f]["shares"]["fable"]["3"] = 0.70
    assert bars(d)[0]["4"] is True


def test_bar5_top_rung():
    flip(lambda d: d["families"]["istack"]["shares"]["fable"].__setitem__("5", 0.5), "5")


def test_bar6_noise_factor_and_missing():
    flip(lambda d: d["noise"].__setitem__("measured", 0.151), "6")
    d = good()
    d["noise"]["measured"] = 0.15                             # exactly 1.5x passes
    assert bars(d)[0]["6"] is True
    del d["noise"]
    assert bars(d)[0]["6"] is False


def test_missing_inputs_fail_closed():
    b, r = bars({})
    assert not r["ok"] and not any(b.values())


def test_input_is_not_mutated():
    d = good()
    before = copy.deepcopy(d)
    pilot_bars.evaluate(d)
    assert d == before


def run(path, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *extra], capture_output=True, text=True)


def test_cli_exit_codes(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(good()), encoding="utf-8")
    r = run(p)
    assert r.returncode == 0 and "G-Headroom: PASS" in r.stdout
    d = good()
    d["scores"]["fable"]["code"] = 9.0
    p.write_text(json.dumps(d), encoding="utf-8")
    r = run(p, "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["bars"]["1"]["ok"] is False
    assert run(tmp_path / "nope.json").returncode == 2
    p.write_text("[1]", encoding="utf-8")
    assert run(p).returncode == 2
