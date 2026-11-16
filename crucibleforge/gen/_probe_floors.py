"""Helper of gen/probe.py: the red-team floors of the fast-fail question PROBE tier 3 (t-work/probe/redteam3.py) as source strings the engine
grader runs: a constant guess, the linear (no cubic, no friction) closed form, 2000 dense readings scaled by their mean, and the ff baseline.
Pinned by ``_FLOORS_SHA`` in probe.py."""
from . import _probe_ff as _ff

ZERO_CONST = "def solve(measure, queries):\n    return [0.15 for a, t in queries]\n"
ZERO_LIN = ("import math\ndef solve(measure, queries):\n    k=4.0; c=0.5; w=math.sqrt(k); z=c/(2*w); wd=w*math.sqrt(1-z*z)\n"
            "    return [a/k*(1-math.exp(-z*w*t)*(math.cos(wd*t)+z*w/wd*math.sin(wd*t))) for a,t in queries]\n")
DENSE = ("import math\ndef solve(measure, queries):\n    s=0.0\n    for i in range(2000):\n        s+=measure(0.2+0.8*(i%10)/9, 0.005*i)\n"
         "    return [s/2000*a for a,t in queries]\n")


def floors(inst):
    """{name: source}"""
    return {"zero_const": ZERO_CONST, "zero_lin": ZERO_LIN, "dense2000": DENSE, "baseline": _ff.baseline(inst)}
