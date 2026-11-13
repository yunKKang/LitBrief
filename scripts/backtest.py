"""Resume a calendar-month evaluation, serially and without publishing content."""
import argparse
from contextlib import closing
from datetime import date,timedelta
import hashlib
import json

from library import ROOT,DATA,connect,worker_lock,publication_day,source_cycle,period,now,identity_conflicts
from automation import cycle,inspect,policy_hash
from usage_report import report as usage
from webapp import atomic_write


def windows(month):
    first=date.fromisoformat(month+'-01')
    next_month=(first.replace(day=28)+timedelta(days=4)).replace(day=1)
    last=next_month-timedelta(days=1);day=first;result=[]
    while day<=last:
        _,end=period(day)
        # A trailing partial week waits for a full weekly window, but the data
        # cutoff can still be explicitly shortened by the caller in future work.
        if date.fromisoformat(end)>last:
            raise ValueError('This backtest runner currently requires a month ending on Saturday')
        result.append({'kind':'weekly','start':day.isoformat(),'end':end})
        day=date.fromisoformat(end)+timedelta(days=1)
    result.append({'kind':'monthly','start':first.isoformat(),'end':last.isoformat()})
    return result


def state(db,window):
    rows=[dict(r) for r in db.execute("SELECT * FROM papers WHERE cohort='baseline' ORDER BY id")
          if source_cycle(r['journal'])==window['kind'] and window['start']<=(publication_day(json.loads(r['data'])) or '')<=window['end']]
    ids={r['id'] for r in rows}
    gates=inspect(db,cohort='baseline',kind=window['kind'],ids=ids)
    states={}
    for r in rows:states[r['state']]=states.get(r['state'],0)+1
    runnable=0
    for r in rows:
        if r['human_decision'] or identity_conflicts(db,r['id']):continue
        result=json.loads(r['result'] or '{}')
        if r['state']=='pending':runnable+=1
        elif r['state']=='awaiting_abstract':
            schedule=db.execute('SELECT next_attempt_at FROM enrichment_schedule WHERE paper_id=?',(r['id'],)).fetchone()
            if not result.get('title_screened') or not schedule or (schedule[0] and schedule[0]<=now()):runnable+=1
        elif r['state']=='awaiting_review' and result.get('policy_hash')!=policy_hash():runnable+=1
    runnable+=sum(g['reason'] in {'verification_missing_or_stale','verification_incomplete'} for g in gates)
    stamp=hashlib.sha256(json.dumps([(r['id'],r['revision'],r['state']) for r in rows]+[(g['paper_id'],g['reason']) for g in gates]).encode()).hexdigest()
    return {'records':len(rows),'states':states,'runnable':runnable,'eligible':sum(g['eligible'] for g in gates),
            'gates':gates,'fingerprint':stamp}


def evaluate(db, month, max_tokens, folder, accepted_unknown_requests=()):
    folder.mkdir(parents=True,exist_ok=True)
    progress=folder/'progress.json'
    initial=usage(db)
    plan=json.loads(progress.read_text('utf-8')) if progress.exists() else {
        'month':month,'started_at':now(),'initial_usage':initial['total'],
        'initial_unknown':initial['unknown'],'windows':windows(month),'runs':[]}
    if plan['month']!=month:raise ValueError('Checkpoint month does not match request')
    for request_id in accepted_unknown_requests:
        row=db.execute('SELECT status FROM ai_requests WHERE id=?',(request_id,)).fetchone()
        if not row or row['status']!='billing_unknown':
            raise ValueError('Only a known billing-unknown request may be explicitly isolated')
    plan['accepted_unknown_requests']=sorted(set(plan.get('accepted_unknown_requests',[]))|set(accepted_unknown_requests))
    plan['soft_token_threshold']=max_tokens
    def save(status):
        current=usage(db);plan.update(status=status,updated_at=now(),spent_tokens=current['total']-plan['initial_usage'],
          unknown_calls_added=current['unknown']-plan['initial_unknown'],
          accepted_unknown_still_unpriced=len(set(current.get('unknown_request_ids',[]))&set(plan['accepted_unknown_requests'])))
        atomic_write(progress,json.dumps(plan,ensure_ascii=False,indent=2))
    save('running')
    for window in plan['windows']:
        # Re-evaluate checkpoints: new material or a repaired pending check can
        # make a previously exhausted window runnable without redoing paid work.
        for attempt in range(100):
            before=state(db,window)
            window['result']=before
            window['finished']=not before['runnable']
            window['unresolved_material']=any(before['states'].get(s,0) for s in ('awaiting_abstract','needs_material','ai_error','needs_review'))
            window['blocked_admission']=sum(not g['eligible'] for g in before['gates'])
            save('running')
            if window['finished']:
                window.pop('stop_reason',None)
                break
            if plan['unknown_calls_added']>plan['accepted_unknown_still_unpriced']:
                save('billing_needs_attention');return plan
            if plan['spent_tokens']>=max_tokens:
                save('soft_threshold_reached');return plan
            result=cycle(db,live=True,max_tokens=max_tokens-plan['spent_tokens'],max_groups=12,
                         window=(window['start'],window['end']),kind=window['kind'],cohort='baseline',discover=False)
            plan['runs'].append(result['cycle_id'])
            atomic_write(folder/(result['cycle_id']+'.json'),json.dumps(result,ensure_ascii=False,indent=2))
            after=state(db,window);window['result']=after;save('running')
            print(json.dumps({'window':window['start'],'kind':window['kind'],'spent_tokens':plan['spent_tokens'],
                'states':after['states'],'eligible':after['eligible'],'runnable':after['runnable']},ensure_ascii=False),flush=True)
            if result['execution_status']!='completed':
                save('execution_needs_attention');return plan
            if after['runnable'] and after['fingerprint']==before['fingerprint']:
                # A queue with pending work is never labelled completed merely
                # because it stopped changing. Other windows may still proceed.
                window['stop_reason']='no_progress'
                break
            window.pop('stop_reason',None)
        else:
            save('iteration_guard');return plan
    save('evaluated_with_blocked_work' if any(not w.get('finished') for w in plan['windows']) else 'evaluated_available_material')
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month',required=True)
    parser.add_argument('--max-tokens',required=True,type=int)
    parser.add_argument('--isolate-unknown-request',action='append',default=[],
                        help='Explicitly accept unknown cost for this request while leaving its papers quarantined; never retries it')
    args=parser.parse_args()
    if args.max_tokens<=0:parser.error('A positive soft token threshold is required')
    with worker_lock(DATA),closing(connect()) as db:
        plan=evaluate(db,args.month,args.max_tokens,ROOT/'outputs/backtests'/args.month,args.isolate_unknown_request)
    print(json.dumps({'status':plan['status'],'spent_tokens':plan['spent_tokens'],'published':False}),flush=True)
    if plan['status']!='evaluated_available_material':raise SystemExit(2)


if __name__=='__main__':main()
