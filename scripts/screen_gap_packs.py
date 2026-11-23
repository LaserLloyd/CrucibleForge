"""Cheap screen of the three staged GAP packs (patchgap, toolgap, everygap) against one hosted model label.

Staged ladder cases cannot be named in `crucibleforge run --cases` (the run's case list is the composition's), so this
drives the families directly: materialize, call the provider, grade with the family's own checker (fixcheck for
patchgap, graders.grade_checks for everygap, the console session for toolgap). Usage, from the worktree, with the
provider key env sourced (names only: DEEPSEEK_API_KEY / MINIMAX_API_KEY / OPENROUTER_API_KEY):

    uv run python scripts/screen_gap_packs.py <model label> [run_every,run_tool,run_patch]

Do NOT source the release env file first: it sets a spend governor that answers 401 outside the CLI.
Prints one JSON line per case and a token/price summary from the config's price block (reply text of patch cases
goes to $SCREEN_OUT/). Config path: $CRUCIBLEFORGE_CONFIG.
"""
import os
import json, sys, time, concurrent.futures as cf
sys.path.insert(0, ".")
OUT = os.environ.get("SCREEN_OUT", "screen-out")
from crucibleforge import config, providers, graders, fixcheck
from crucibleforge.gen import toolgap, everygap, patchgap
from crucibleforge.gen._stack_common import split_turns

label = sys.argv[1]
cfg = config.load_config(os.environ["CRUCIBLEFORGE_CONFIG"])
ent = [e for e in config.resolve_models(cfg, label)][0]
prov = providers.get_provider(cfg, ent["provider"])
mid = ent["model_id"]
price = ent.get("price", {})
SEED = "4.5.0"
tot = {"pt": 0, "ct": 0}

def chat(msgs, mx):
    r = prov.chat(mid, msgs, max_tokens=mx, temperature=0.0)
    tot["pt"] += r.prompt_tokens or 0
    tot["ct"] += r.completion_tokens or 0
    return r.response_text or "", r

def run_every(lv, i):
    g = {"type": "everygap", "level": lv, "key": f"scr{i}"}
    cid = f"EG-{lv}-{i}"
    m = everygap.materialize(g, release_seed=SEED, case_id=cid)
    txt, r = chat([{"role": "system", "content": m["system"]}, {"role": "user", "content": m["prompt"]}], 16384)
    v = graders.grade_checks(txt, m["grader_config"])
    return ("everygap", lv, i, v["grade"] == "pass", v.get("rate"), [x["id"] for x in v["results"] if not x["pass"]][:4])

def run_patch(lv, i):
    g = {"type": "patchgap", "level": lv, "key": f"scr{i}"}
    cid = f"PG-{lv}-{i}"
    m = patchgap.materialize(g, release_seed=SEED, case_id=cid)
    txt, r = chat([{"role": "system", "content": m["system"]}, {"role": "user", "content": m["prompt"]}], 65536)
    open(f"{OUT}/reply-pg-{label}-{lv}-{i}.txt", "w", encoding="utf-8").write(txt)
    v = fixcheck.grade_answer(txt, m["grader_config"], finish_reason=r.finish_reason)
    return ("patchgap", lv, i, v["score"] == 1.0, v["score"], [x["id"] for x in v.get("results", []) if not x.get("pass")][:6], {"fmt": v.get("format_errors"), "applied": v.get("applied"), "fin": r.finish_reason, "ct": r.completion_tokens, "chars": len(txt)})

def run_tool(lv, i):
    g = {"type": "toolgap", "level": lv, "key": f"scr{i}"}
    cid = f"TG-{lv}-{i}"
    m = toolgap.materialize(g, release_seed=SEED, case_id=cid)
    sess = toolgap.new_session(m["console"]["key"])
    msgs = [{"role": "system", "content": m["system"]}, {"role": "user", "content": m["prompt"]}]
    while sess.state == "running":
        txt, r = chat(msgs, 8192)
        out = sess.step_reply(txt)
        msgs.append({"role": "assistant", "content": txt})
        if sess.state != "running":
            break
        msgs.append({"role": "user", "content": out})
    cr = toolgap.console_result(sess)
    return ("toolgap", lv, i, cr["score"] == 1.0, cr["score"], [e["id"] for e in cr["elements"] if not e["pass"]][:6])

jobs = [(run_every, lv, i) for lv in (1, 2) for i in (1, 2, 3)] + \
       [(run_tool, lv, i) for lv in (1, 2, 3) for i in (1, 2)] + \
       [(run_patch, lv, i) for lv in (1, 2) for i in (1, 2)]
if len(sys.argv) > 2:
    jobs = [j for j in jobs if j[0].__name__ in sys.argv[2].split(",")]
res = []
with cf.ThreadPoolExecutor(4) as ex:
    futs = {ex.submit(f, lv, i): (f.__name__, lv, i) for f, lv, i in jobs}
    for fu in cf.as_completed(futs):
        try:
            r = fu.result()
        except Exception as e:  # noqa
            r = (futs[fu], f"ERROR {type(e).__name__}: {str(e)[:200]}")
        print(json.dumps(r), flush=True)
        res.append(r)
cost = tot["pt"] / 1e6 * price.get("input", 0) + tot["ct"] / 1e6 * price.get("output", 0)
print(json.dumps({"label": label, "prompt_tokens": tot["pt"], "completion_tokens": tot["ct"], "usd_est": round(cost, 4)}))
