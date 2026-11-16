"""One reply, one grade: the IMPLIED sandbox seeds `random` and pins PYTHONHASHSEED/TZ/locale, so a reply that uses
randomness, str hash() or set order gives identical outputs (and the same grade) on every run (lead 2026-11-14:
Grok's calibration reply with unseeded random.choice graded .7037/.7407/.7778 on three regrades)."""
from crucibleforge.gen import implied as I

NOISY = '''
import random, os, time, uuid
def _noise():
    s = {"alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"}
    return [random.random(), random.choice(["a", "b", "c", "d", "e"]), hash("implied") % 1000003, list(s),
            os.environ.get("TZ"), os.environ.get("PYTHONHASHSEED"), time.strftime("%Z", time.localtime(0)),
            random.Random().random(), os.urandom(8).hex(), str(uuid.uuid4())]
'''


def _code(inst):
    fns = list(inst["public"]["fn"].values())
    return NOISY + "".join(f"\ndef {f}(*a, **k):\n    return _noise()\n" for f in fns)


def test_noisy_reply_gives_identical_outputs_and_grades_three_times():
    inst = I._instance(5, 1, "pay", 0)
    code = _code(inst)
    runs = [I._run(code, inst) for _ in range(3)]
    assert all(r["ok"] for r in runs), [r["err"] for r in runs]
    assert runs[0]["out"] == runs[1]["out"] == runs[2]["out"]
    grades = [I._judge_all(inst, code) for _ in range(3)]
    grades = [(g["q"], [(x["id"], x["pass"]) for x in g.get("results", [])]) for g in grades]
    assert grades[0] == grades[1] == grades[2]
    first = next(iter(runs[0]["out"].values()))[0]      # one entry per call
    assert first[4] == "UTC" and first[5] == "0"
