"""Provider-reported usage, reconciled by request ID; never an invoice estimate."""
import json
from pathlib import Path

from library import ROOT


def report(db, log_path=None):
    records={}
    cache_reuses=0
    for row in db.execute('SELECT id,status,usage_json,detail FROM ai_requests'):
        detail=json.loads(row['detail'])
        if detail.get('cache_reused'):
            cache_reuses+=1
            records[row['id']]={'skip':True}
        else:
            records[row['id']]={'usage':json.loads(row['usage_json'] or 'null'),'status':row['status']}
    path=Path(log_path or ROOT/'outputs/ai-trial/usage.jsonl')
    incomplete_lines=0
    if path.is_file():
        for number,line in enumerate(path.read_text('utf-8').splitlines(),1):
            try:event=json.loads(line)
            except ValueError:
                incomplete_lines+=1;continue
            key=event.get('request_id') or f'legacy-line-{number}'
            if key in records:
                if not records[key].get('skip') and not records[key].get('usage'):
                    records[key]['usage']=event.get('usage')
                continue
            records[key]={'usage':event.get('usage'),'status':event.get('state'),'legacy':not bool(event.get('request_id'))}
    totals={k:0 for k in ('calls','input','output','total','reasoning','cached','unknown','legacy_calls')}
    anomalies=[]
    unknown_ids=[]
    for key,row in records.items():
        if row.get('skip'):continue
        totals['calls']+=1;totals['legacy_calls']+=bool(row.get('legacy'))
        usage=row.get('usage') or {}
        i,o,t=[usage.get(k) for k in ('input_tokens','output_tokens','total_tokens')]
        valid=lambda n:type(n) is int and n>=0
        if valid(t):totals['total']+=t
        elif valid(i) and valid(o):totals['total']+=i+o
        else:
            totals['unknown']+=1
            if not row.get('legacy'):unknown_ids.append(key)
        if valid(i):totals['input']+=i
        if valid(o):totals['output']+=o
        if all(valid(n) for n in (i,o,t)) and t!=i+o:anomalies.append(key)
        for output,container,field in [('reasoning','output_tokens_details','reasoning_tokens'),('cached','input_tokens_details','cached_tokens')]:
            value=(usage.get(container) or {}).get(field)
            if valid(value):totals[output]+=value
    return {**totals,'unknown_request_ids':unknown_ids,'cache_reuses':cache_reuses,'arithmetic_mismatches':anomalies,'incomplete_log_lines':incomplete_lines,
            'basis':'provider-reported; reasoning is part of output, cached is part of input; missing usage is not zero cost'}
