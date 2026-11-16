"""COMPACTOR (FC3) as an engine family (T7, gate note F5).

The simulator and its Session loop live in ``compactor2`` (ported from jx-c, frozen); this module registers
it with the generator registry so a profile can name it (``generator: {type: compactor, key: s1}``) and the
interactive runner can drive it. The case is INTERACTIVE: the model answers one console command per reply and
the simulator answers back (runner ``_run_console_loop``); there is no scripted ``turns`` list.

* ``materialize``: the opening prompt (intro) and the system line, plus ``console = {family, key}`` which is
  what the runner keys on. The instance is ``compactor2.generate(key)`` (the frozen seeds s1..s3 are the keys;
  the simulator is deterministic from the key and independent of the release seed, as frozen).
* ``score_replies``: replay a list of replies through a fresh Session and score it (what the runner's loop
  computes live; also the baselines and the reference for ``verify``).
* ``reference`` / ``mutants`` are replies joined by the multi-turn separator: the reference is the competent path
  (scores 85.0), the mutants are the generic baselines (read-everything, halt-all-running).
"""
from __future__ import annotations

from . import compactor2 as c2
from ._stack_common import TURN_SEP, join_turns, split_turns

FAMILY = "compactor"
LEVELS = {1: {"answer_tokens": 60, "max_turns": c2.MAX_TURNS}}
SYSTEM = "You are the night operator at a facility console. Reply with one console command per turn."
#: sampled output per reply is a command line; the reply budget is a few hundred tokens with thinking off
MAX_TOKENS_PER_TURN = 4096
#: the item is graded /100 by the simulator; a row is a "pass" only when it halts the right press cleanly
PASS_SCORE = 85.0
#: the reference path scores exactly 85.0 (/100): not an oracle (verify accepts the declared band)
REFERENCE_GRADE = (0.85, 0.85)


def _key(gen: dict) -> str:
    return str(gen.get("key") or "s1")


def instance(key: str) -> c2.Instance:
    return c2.generate(str(key))


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    inst = instance(_key(gen))
    return {"system": SYSTEM, "prompt": c2.intro(inst),
            "console": {"family": FAMILY, "key": _key(gen)},
            "grader": "console", "grader_config": {"key": _key(gen)},
            "max_tokens": MAX_TOKENS_PER_TURN,
            "meta": {"turns_close": c2.CLOSE_TURN, "turns_max": c2.MAX_TURNS, "ref_turns": inst.ref_turns,
                     "baseline_note": "read-everything 38.7 on s1-s3 (31.2 on s1-s6); see T7-WIRING.md"}}


def new_session(key: str) -> c2.Session:
    return c2.Session(instance(key))


def score_replies(key: str, replies: list[str]) -> dict:
    s = new_session(key)
    for r in replies:
        if s.state != "running":
            break
        s.step_reply(r)
    return c2.score(s)


def _replies_of(sess: c2.Session) -> list[str]:
    return list(getattr(sess, "replies", []))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_replies_of(c2.reference(instance(_key(gen)))))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    inst = instance(_key(gen))
    return [join_turns(_replies_of(c2.baseline_read_everything(inst))),
            join_turns(_replies_of(c2.baseline_halt_all_running(inst)))]


def grade(answer: str, materialized: dict) -> float:
    key = materialized["console"]["key"]
    return float(score_replies(key, split_turns(answer))["score"]) / 100.0
