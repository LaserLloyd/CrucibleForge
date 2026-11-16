"""T2 2d: re-score stored CQ1-3 rows with the artefact fixes (source rule = one group; truncated rows dropped)."""
import sys, json, glob, collections, re, copy, time
sys.path.insert(0, '/var/tmp/mb45-build/wt-t2')
from crucibleforge import graders
import os
_PYD = os.path.realpath(os.path.expanduser('~/.local/share/uv/python/cpython-3.13.14-linux-x86_64-gnu'))
graders._PY_ROOT = _PYD   # stored rows were graded under Python 3.13 (3.14 changed ',' grouping in fractional parts)
graders._REAL_PY = os.path.join(_PYD, 'bin', 'python3.13')
RES = os.path.join(os.environ.get('CF_RESULTS_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'results')), '')
case = {c['id'][:3]: c for c in json.load(open('/var/tmp/mb45-build/wt-t2/cases/ladder/work/code/classic-code.json'))['cases'] if isinstance(c, dict)}

def fix_cfg(cfg):
    cfg = copy.deepcopy(cfg)
    lines = cfg['tests'].split('\n')
    i = next(k for k, l in enumerate(lines) if l.startswith('for _n in _ast.walk(_tree):'))
    j = i + 1
    while j < len(lines) and (lines[j].strip() == '' or lines[j][0] in ' \t'):
        j += 1
    block = ['    ' + l if l.strip() else l for l in lines[i:j]]
    new = lines[:i] + ['_RULE_VIOL = None', 'try:'] + block + ['except AssertionError as _e:', '    _RULE_VIOL = str(_e)'] + lines[j:]
    cfg['tests'] = '\n'.join(new)
    tg = dict(cfg['test_groups']); tg['source-rule'] = 'assert _RULE_VIOL is None, _RULE_VIOL\n'
    cfg['test_groups'] = tg
    return cfg
FIX = {k: fix_cfg(v['grader_config']) for k, v in case.items()}

def main(models):
    latest = {}
    out = []
    for f in sorted(glob.glob(RES + 'transcripts_*.jsonl')):
        m = f[len(RES) + len('transcripts_'):-6]
        if models and m not in models: continue
        with open(f, errors='replace') as fh:
            for l in fh:
                if '"case_id": "CQ' not in l[:4000]: continue
                r = json.loads(l)
                if not r['case_id'].startswith('CQ'): continue
                out.append((m, r))
    # keep the latest bench_revision per (model, case)
    best = {}
    for m, r in out:
        k = (m, r['case_id'][:3]); best.setdefault(k, []).append(r)
    res = []
    for (m, c), rs in sorted(best.items()):
        rev = max(x.get('bench_revision') or '' for x in rs)
        for r in rs:
            if (r.get('bench_revision') or '') != rev: continue
            fr = r.get('finish_reason')
            if fr == 'error': continue
            el = r.get('elements') or {}
            old = el.get('rate') if isinstance(el, dict) and el.get('rate') is not None else (1.0 if r.get('grade') == 'pass' else 0.0)
            ct = (r.get('metrics') or {}).get('completion_tokens')
            row = dict(model=m, case=c, rep=r.get('repeat'), finish=fr, truncated=(fr == 'length' or bool(r.get('truncated'))), tokens=ct,
                       secs=(r.get('metrics') or {}).get('total_s'), old=old, rev=rev)
            if row['truncated']:
                row['new'] = None
            else:
                t = time.time()
                row['ctrl'] = graders.grade_python_exec(r.get('response') or '', case[c]['grader_config'], fr).get('rate')
                g = graders.grade_python_exec(r.get('response') or '', FIX[c], fr)
                rs_ = {x['id']: x for x in g.get('results', [])}
                row['new'] = g.get('rate'); row['rule'] = (rs_.get('source-rule') or {}).get('detail') if rs_.get('source-rule') and not rs_['source-rule']['pass'] else None
                row['groups'] = {k: v['pass'] for k, v in rs_.items()}
                row['detail'] = g.get('detail', '')[:200]; row['gsecs'] = round(time.time() - t, 1)
            res.append(row)
            print(json.dumps(row), flush=True)
    return res
if __name__ == '__main__':
    main(set(sys.argv[1:]))
