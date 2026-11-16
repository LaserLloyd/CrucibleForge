import json, collections, statistics
rows=[json.loads(l) for l in open('/var/tmp/mb45-build/tmp-t2/rescore_cq.jsonl') if l.startswith('{')]
by=collections.defaultdict(lambda: collections.defaultdict(list))
mism=0
for r in rows:
    if r['truncated']:
        by[r['model']][r['case']].append(('T',r['old'],None,r['tokens'])); continue
    by[r['model']][r['case']].append(('V',r['old'],r['new'],r['tokens']))
    if r.get('ctrl') is not None and abs(r['ctrl']-r['old'])>1e-9: mism+=1
print('control mismatches (stored vs original grader under py3.13):',mism,'of',sum(1 for r in rows if not r['truncated']))
rule=[(r['model'],r['case'],r['rule']) for r in rows if r.get('rule')]
print('source-rule violations:',len(rule)); 
for x in rule: print('  ',x[0],x[1],x[2][:90])
tbl={}
for m,cs in sorted(by.items()):
    out={}
    for c,v in cs.items():
        val=[x for x in v if x[0]=='V']
        out[c]=(round(statistics.mean(x[2] for x in val),2) if val else None, round(statistics.mean(x[1] for x in v),2), len(val), len(v))
    new=[o[0] for o in out.values() if o[0] is not None]
    old_all=[o[1] for o in out.values()]
    tbl[m]=(round(statistics.mean(new),2) if new else None, len(new), round(statistics.mean(old_all),2), out)
    print(f"{m:48s} new_trio={tbl[m][0]} (cases valid {len(new)}) old_trio={tbl[m][2]}  ", {c:f"{o[0]}|{o[1]} v{o[2]}/{o[3]}" for c,o in sorted(out.items())})
full=[(m,t[0]) for m,t in tbl.items() if t[1]==3]
print('models valid on all 3 cases:',len(full),'range',min(x[1] for x in full),max(x[1] for x in full), 'sd',round(statistics.pstdev([x[1] for x in full]),3))
