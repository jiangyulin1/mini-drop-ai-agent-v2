#!/usr/bin/env python3
"""Run native smolagents ToolCallingAgent for 9x3 runs."""
import os, sys, time, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.adapters.smolagents.native_run_case import run_case
api_key=os.environ.get('DEEPSEEK_API_KEY','').strip()
run_root=ROOT/'benchmark'/'runs-native'
progress=ROOT/'benchmark'/'work'/'native_smolagents_progress.jsonl'
progress.parent.mkdir(parents=True, exist_ok=True)
done=set()
if progress.exists():
    for line in progress.read_text().splitlines():
        try:
            d=json.loads(line)
            if d.get('status')=='completed':
                done.add((d['case_id'], d['repeat']))
        except: pass
for i in range(1,10):
    case_id=f'case-{i:02d}'
    for rep in [1,2,3]:
        if (case_id, rep) in done:
            continue
        seed=301+rep-1
        t=time.time()
        try:
            r=run_case(case_id, rep, seed, run_root, api_key)
            print(case_id, rep, r['status'], f'{time.time()-t:.1f}s', flush=True)
            with progress.open('a') as f:
                f.write(json.dumps({'case_id':case_id,'repeat':rep,'status':r['status'],'run_id':r['run_id']})+'\n')
        except Exception as e:
            print(case_id, rep, 'ERROR', type(e).__name__, e, flush=True)
            with progress.open('a') as f:
                f.write(json.dumps({'case_id':case_id,'repeat':rep,'status':'error','error':str(e)})+'\n')
