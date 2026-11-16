"""P1 probe for C1 v2 (turn clock) on the Claude Code subscription. One command per reply.

  uv run python probe-c/p1c2.py --model claude-haiku-5-5 --seed s1 --out DIR
"""
import argparse, json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from crucibleforge import claudecode  # noqa: E402
from crucibleforge.gen import compactor2 as c  # noqa: E402

EFFORT = os.environ.get("CRUCIBLEFORGE_CC_EFFORT", "high")
_orig = claudecode.build_argv
claudecode.build_argv = lambda *a, **k: _orig(*a, **k) + ["--effort", EFFORT]
SYSTEM = "You are the night operator at a facility console. Reply with one console command per turn."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    inst = c.generate(a.seed)
    s = c.Session(inst)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": c.intro(inst)}]
    log, tok = [], 0
    path = out / f"{a.model}-{a.seed}.json"
    while s.state == "running":
        t0 = time.time()
        try:
            r = claudecode.run_chat(a.model, msgs, timeout=900)
        except Exception as e:
            log.append({"error": repr(e)}); break
        nxt = s.step_reply(r.response_text)
        tok += r.completion_tokens
        log.append({"turn": s.turn, "reply": r.response_text, "out_tokens": r.completion_tokens,
                    "wall_s": round(time.time() - t0, 1), "result": nxt})
        msgs += [{"role": "assistant", "content": r.response_text}, {"role": "user", "content": nxt}]
        path.write_text(json.dumps({"partial": True, "turns": log}, indent=1))
    res = c.score(s) if s.state != "running" else {"score": None, "state": "infra"}
    res.update(model=a.model, seed=a.seed, out_tokens=tok, faults=s.faults,
               target=c.o(inst.target.room.loc, 3), target_dev=c.o(inst.target.code))
    path.write_text(json.dumps({"result": res, "turns": log}, indent=1))
    print(json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
