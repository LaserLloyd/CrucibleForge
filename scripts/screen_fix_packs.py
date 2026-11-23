"""Cheap screen of the staged Fixes pack (patchtough) or its reference family (patchmod) against one hosted model label.

    uv run python scripts/screen_fix_packs.py <model label> <family: patchtough|patchmod> <levels e.g. 1,2> <keys e.g. a,b,c>

Provider key from the environment (names only: DEEPSEEK_API_KEY / MINIMAX_API_KEY / OPENROUTER_API_KEY); do NOT source
the release env file (its spend governor answers 401 here). Config path: $CRUCIBLEFORGE_CONFIG (the models file of the runs). One JSON line per
cell and a token/price line; replies go to $SCREEN_OUT/. Same driver shape as scripts/screen_gap_packs.py.
"""
import concurrent.futures as cf
import json
import os
import sys

sys.path.insert(0, ".")
OUT = os.environ.get("SCREEN_OUT", "screen-out")
from crucibleforge import config, fixcheck, providers
from crucibleforge.gen import patchmod, patchtough

label, fam, levels, keys = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")], sys.argv[4].split(",")
mod = {"patchtough": patchtough, "patchmod": patchmod}[fam]
cfg = config.load_config(os.environ["CRUCIBLEFORGE_CONFIG"])
ent = [e for e in config.resolve_models(cfg, label)][0]
prov = providers.get_provider(cfg, ent["provider"])
mid, price = ent["model_id"], ent.get("price", {})
tot = {"pt": 0, "ct": 0}


def cell(lv, key):
    g = {"type": fam, "level": lv, "key": key}
    cid = f"{fam[:5].upper()}-{lv}-{key}"
    m = mod.materialize(g, release_seed="4.5.0", case_id=cid)
    r = prov.chat(mid, [{"role": "system", "content": m["system"]}, {"role": "user", "content": m["prompt"]}],
                  max_tokens=65536, temperature=0.0)
    tot["pt"] += r.prompt_tokens or 0
    tot["ct"] += r.completion_tokens or 0
    txt = r.response_text or ""
    open(f"{OUT}/reply-{fam}-{label}-{lv}-{key}.txt", "w", encoding="utf-8").write(txt)
    v = fixcheck.grade_answer(txt, m["grader_config"], finish_reason=r.finish_reason)
    return [fam, label, lv, key, v["score"], [x["id"] for x in v.get("results", []) if not x.get("pass")][:8],
            {"fmt": v.get("format_errors"), "applied": v.get("applied"), "fin": r.finish_reason, "ct": r.completion_tokens}]


jobs = [(lv, k) for lv in levels for k in keys]
with cf.ThreadPoolExecutor(4) as ex:
    futs = {ex.submit(cell, lv, k): (lv, k) for lv, k in jobs}
    for fu in cf.as_completed(futs):
        try:
            r = fu.result()
        except Exception as e:  # noqa
            r = [fam, label, futs[fu], f"ERROR {type(e).__name__}: {str(e)[:200]}"]
        print(json.dumps(r), flush=True)
cost = tot["pt"] / 1e6 * price.get("input", 0) + tot["ct"] / 1e6 * price.get("output", 0)
print(json.dumps({"label": label, "prompt_tokens": tot["pt"], "completion_tokens": tot["ct"], "usd_est": round(cost, 4)}))
