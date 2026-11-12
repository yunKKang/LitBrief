"""Deterministic orchestration. Models propose; code validates admission and publication.

Default cycle is offline shadow mode. Live processing and local publication are explicit,
separate options. This module never deploys to the remote website or changes runtime mode.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from library import (DATA, ROOT, TOPICS, now, today, registry, period, publication_day,
                     worker_lock, unresolved_ai, build_issue, artifact_path, source_cycle, issue_window, identity_conflicts)
from quality import policy

AI_CACHE = ROOT / 'outputs/ai-trial'


def policy_hash():
    return hashlib.sha256(policy().encode()).hexdigest()


def admission(db, row, day=None, issue_end=None, *, cohort='monitoring', kind='weekly'):
    """Return a reasoned decision for exactly this record; never mutate it."""
    day = day or today()
    data, result = json.loads(row['data']), json.loads(row['result'] or '{}')
    def no(reason): return {'eligible':False,'reason':reason,'paper_id':row['id']}
    if row['cohort']!=cohort or row['journal'] not in registry(): return no('inactive_or_outside_monitoring')
    if source_cycle(row['journal'])!=kind:return no('different_publication_cycle')
    if row['human_decision']=='reject': return no('human_exclusion')
    if row['state'] not in {'awaiting_review','approved'}: return no('not_final_candidate')
    if row['material_changed']: return no('material_changed')
    from prefilter import exclusion
    if exclusion(data): return no('excluded_article_type')
    if unresolved_ai(db,row['id']): return no('unresolved_request')
    if identity_conflicts(db,row['id']):return no('identity_conflict')
    pub_day = publication_day(data)
    if not pub_day or pub_day>day.isoformat(): return no('publication_date_unverified')
    if not data.get('abstract') or not data.get('abstract_source'): return no('missing_abstract')
    if not result.get('summary') or result.get('decision')!='include': return no('missing_recommendation')
    topics=json.loads(row['topics'])
    if not isinstance(topics,list) or not topics or any(t not in TOPICS for t in topics) or not data.get('doi'): return no('missing_identity_or_topics')
    feedback=db.execute('SELECT * FROM feedback WHERE paper_id=? ORDER BY id DESC LIMIT 1',(row['id'],)).fetchone()
    if feedback:
        if feedback['category'] in {'duplicate','summary_issue'}: return no('feedback_requires_resolution')
        if feedback['category']=='low_priority':
            feedback_day=datetime.fromisoformat(feedback['at']).astimezone(timezone(timedelta(hours=8))).date()
            if (issue_end or period(day)[1]) <= period(feedback_day)[1]: return no('deferred_by_feedback')
    if row['human_decision']=='approve' and row['approval_actor']=='human':
        return {'eligible':True,'paper_id':row['id'],'reason':'current_human_approval','actor':'human'}
    if not result.get('title_zh'): return no('missing_recommendation')
    if result.get('policy_hash') != policy_hash(): return no('screening_policy_unverified')
    request=db.execute('SELECT * FROM ai_requests WHERE id=?',(result.get('request_id'),)).fetchone()
    if not request or request['mode']!='abstracts' or request['status']!='applied': return no('screening_provenance_missing')
    if policy() not in json.loads(request['request_json']).get('instructions',''): return no('screening_policy_unverified')
    inputs=[r for r in json.loads(request['inputs']) if r['id']==row['id']]
    if len(inputs)!=1 or inputs[0].get('screening_policy_hash')!=policy_hash(): return no('screening_policy_unverified')
    old_data=json.loads(inputs[0]['data'])
    if any(old_data.get(k)!=data.get(k) for k in ('doi','title','type','abstract','abstract_source')):
        return no('screening_material_mismatch')
    from screen_paper import parse_response, SCHEMA
    try:
        core={k:result.get(k) for k in SCHEMA['properties']}
        parse_response({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(core)}]}]},data['abstract'])
    except (ValueError,TypeError): return no('invalid_screening_evidence')
    approved=result.get('automatic_approval',{}) if row['approval_actor']=='agent' else {}
    revision=approved.get('verified_revision',row['revision'])
    if approved and row['revision']!=revision+1: return no('approval_revision_changed')
    jobs=db.execute("""SELECT quality_jobs.*,quality_batches.metadata FROM quality_jobs
      JOIN quality_batches ON quality_batches.id=batch_id WHERE paper_id=? AND mode='verify'
      ORDER BY quality_jobs.rowid DESC""",(row['id'],)).fetchall()
    for job in jobs:
        snapshot=json.loads(job['snapshot'])
        if snapshot['revision']!=revision or json.loads(job['metadata'])['policy_hash']!=policy_hash(): continue
        if job['status']!='checked': return no('verification_incomplete')
        verdict=json.loads(job['result'] or '{}')
        if verdict.get('verdict')!='supported' or verdict.get('actor')!='agent': return no('verification_not_supported')
        previous=json.loads(snapshot['result'] or '{}')
        if any(previous.get(k)!=result.get(k) for k in ('summary','title_zh','evidence','decision','topics')):
            return no('verification_summary_mismatch')
        if snapshot['data']!=row['data']: return no('verification_material_mismatch')
        check=db.execute('SELECT status,mode,request_json FROM ai_requests WHERE id=?',(job['request_id'],)).fetchone()
        if not check or check['status']!='applied' or check['mode']!='verify': return no('verification_provenance_missing')
        if policy() not in json.loads(check['request_json']).get('instructions',''): return no('verification_policy_unverified')
        from quality import validate
        try:
            item={k:verdict.get(k) for k in ('id','verdict','reason','evidence','issues')}
            validate({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':[item]})}]}]},[dict(row)],'verify')
        except (ValueError,TypeError): return no('invalid_verification_evidence')
        return {'eligible':True,'paper_id':row['id'],'reason':'current_agent_checks','actor':'agent',
                'quality_batch':job['batch_id'],'verified_revision':revision}
    return no('verification_missing_or_stale')


def inspect(db, day=None, issue_end=None, *, cohort='monitoring', kind='weekly', ids=None):
    return [admission(db,r,day,issue_end,cohort=cohort,kind=kind) for r in db.execute("SELECT * FROM papers WHERE cohort=? AND state IN ('awaiting_review','approved','needs_review') AND id NOT IN (SELECT paper_id FROM issue_items JOIN issues ON issues.id=issue_id WHERE issues.state='published') ORDER BY id",(cohort,)) if ids is None or r['id'] in ids]


def admit(db, day=None, issue_end=None, *, cohort='monitoring', kind='weekly', ids=None):
    accepted=[]
    with db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM papers WHERE state IN ('awaiting_review','approved') AND id NOT IN (SELECT paper_id FROM issue_items JOIN issues ON issues.id=issue_id WHERE issues.state='published') ORDER BY id").fetchall():
            if ids is not None and row['id'] not in ids:continue
            gate=admission(db,row,day,issue_end,cohort=cohort,kind=kind)
            if not gate['eligible']: continue
            if row['state']!='approved':
                result=json.loads(row['result'])
                result.update(human_reviewed=False,automatic_approval={'verified_revision':gate['verified_revision'],
                              'quality_batch':gate['quality_batch'],'policy_hash':policy_hash()})
                db.execute("UPDATE papers SET state='approved',approval_actor='agent',result=?,revision=revision+1,updated_at=? WHERE id=?",
                           (json.dumps(result,ensure_ascii=False),now(),row['id']))
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'agent-admission','approved',json.dumps(gate)))
            accepted.append(row['id'])
    return accepted


def closed_period(day):
    start,_=period(day)
    anchor=json.loads((ROOT/'config/runtime.json').read_text('utf-8'))['issue_anchor']
    previous=date.fromisoformat(start)-timedelta(days=1)
    return period(previous) if previous.isoformat()>=anchor else None


def export_confirmed(db, output):
    published=[r[0] for r in db.execute("SELECT id FROM issues WHERE state='published' ORDER BY id")]
    if not published: return {'state':'no_eligible_papers'}
    marker=Path(output)/'latest.json'
    if marker.exists():
        try:
            metadata=json.loads(marker.read_text('utf-8'))
            release=(Path(output)/metadata['directory']).resolve()
            from webapp import validate_release
            if release.is_relative_to(Path(output).resolve()) and published==metadata['issues'] and validate_release(release)==metadata['files']:
                return {'state':'already_exported','issue_id':published[-1],'public_directory':str(release)}
        except (ValueError,KeyError,OSError): pass
    from webapp import export_site
    return {'state':'exported','issue_id':published[-1],'public_directory':str(export_site(db,output))}


def publish_ready(db, day, output, *, window=None, cohort='monitoring', kind='weekly', ids=None):
    """Local snapshot publication only; called after the cycle's explicit mode gate."""
    window=window or (closed_period(day) if kind=='weekly' else issue_window(day.replace(day=1)-timedelta(days=1),'monthly'))
    if not window: return {'state':'not_due'}
    start,end=window
    issue_key=period(date.fromisoformat(start))[0] if kind=='weekly' else start[:7]+'-monthly'
    existing=db.execute('SELECT state FROM issues WHERE id=?',(issue_key,)).fetchone()
    if not existing or existing['state']!='published':
        accepted=admit(db,day,end,cohort=cohort,kind=kind,ids=ids)
        accepted=[pid for pid in accepted if publication_day(json.loads(db.execute('SELECT data FROM papers WHERE id=?',(pid,)).fetchone()[0]))<=end]
        if not accepted and not existing: return {**export_confirmed(db,output),'window':window}
        issue_id=build_issue(db,date.fromisoformat(start),only_ids=accepted,cutoff=end,kind=kind)
        with db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck after draft construction, including any human-edited existing draft.
            for row in db.execute('SELECT papers.* FROM papers JOIN issue_items ON paper_id=papers.id WHERE issue_id=?',(issue_id,)).fetchall():
                pub_day=publication_day(json.loads(row['data']))
                if not admission(db,row,day,end,cohort=cohort,kind=kind)['eligible'] or not pub_day or pub_day>end or (ids is not None and row['id'] not in ids):
                    db.execute('DELETE FROM issue_items WHERE issue_id=? AND paper_id=?',(issue_id,row['id']))
            if db.execute('SELECT 1 FROM issue_items WHERE issue_id=?',(issue_id,)).fetchone():
                db.execute('INSERT OR REPLACE INTO issue_notices VALUES(?,?)',(issue_id,
                    f'本次处理窗口：{start} 至 {end}。本期监测仍为部分覆盖。缺材料或未完成核验的文献未纳入，可在后续期次补录；未收录不代表该刊没有相关研究。'))
                db.execute("UPDATE issues SET state='published',published_at=? WHERE id=?",(now(),issue_id))
    # A prior export failure can be retried without changing the published snapshot.
    return export_confirmed(db,output)


def refresh_legacy(db, *, cohort='monitoring', kind='weekly', ids=None):
    """Re-screen only unreviewed final candidates lacking current policy provenance."""
    count=revoked=0
    with db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM papers WHERE state IN ('awaiting_review','approved') AND human_decision='' AND cohort=? AND id NOT IN (SELECT paper_id FROM issue_items JOIN issues ON issues.id=issue_id WHERE issues.state='published')",(cohort,)).fetchall():
            if ids is not None and row['id'] not in ids:continue
            result=json.loads(row['result'] or '{}')
            if row['journal'] not in registry() or unresolved_ai(db,row['id']): continue
            if row['state']=='approved' and row['approval_actor']=='agent' and not admission(db,row,cohort=cohort,kind=kind)['eligible']:
                result.pop('automatic_approval',None)
                db.execute("DELETE FROM issue_items WHERE paper_id=? AND issue_id IN (SELECT id FROM issues WHERE state='draft')",(row['id'],))
                db.execute("UPDATE papers SET state='awaiting_review',approval_actor='',result=?,revision=revision+1 WHERE id=?",(json.dumps(result),row['id']))
                revoked+=1
            if result.get('policy_hash')==policy_hash(): continue
            db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (now(),'policy-refresh','queued',json.dumps({'paper_id':row['id'],'previous_result':result,'previous_revision':row['revision']})))
            db.execute("UPDATE papers SET state='pending',result=?,revision=revision+1,updated_at=? WHERE id=?",
                       (json.dumps({'title_screened':True,'basis':'已有最终候选，按当前规则重筛摘要'}),now(),row['id']))
            count+=1
    return {'queued_for_current_policy':count,'stale_agent_approvals':revoked}


def cycle(db, *, live=False, publish=False, max_tokens=0, max_groups=3, only=None,
          output=None, day=None, runtime=None, window=None, kind='weekly', cohort='monitoring', discover=True):
    cfg=runtime or json.loads((ROOT/'config/runtime.json').read_text('utf-8'))
    if publish and cfg.get('publication_mode')!='automatic':
        raise ValueError('Automatic publication is disabled; runtime mode must explicitly be automatic')
    if max_groups<1 or (live and max_tokens<1): raise ValueError('Live cycles require a positive shared token limit')
    if only and any(seed not in registry() for seed in only): raise ValueError('Unknown journal')
    day=day or today(); cycle_id=uuid4().hex
    if kind not in {'weekly','monthly'} or cohort not in {'monitoring','baseline'}:raise ValueError('Invalid processing scope')
    if cohort=='baseline' and window is None:raise ValueError('Historical processing requires an explicit window')
    if kind=='monthly' and window is None:
        window=issue_window(day.replace(day=1)-timedelta(days=1),'monthly')
    if window:
        start,end=map(date.fromisoformat,window)
        if start>end or end>=day or issue_window(start,kind)[1]!=end.isoformat():
            raise ValueError('Use one closed calendar window, with an optional shortened first week')
        if kind=='monthly' and start.day!=1:raise ValueError('Monthly processing requires a full calendar month')
    result={'cycle_id':cycle_id,'mode':'live' if live else 'offline_shadow','steps':[],
            'publication_requested':publish,'budget_tokens':max_tokens,'spent_tokens':0,'billing_unknown':False,
            'window':window,'kind':kind,'cohort':cohort}
    output=Path(output or ROOT/'outputs/public')
    def step(name,fn):
        try:
            value=fn(); result['steps'].append({'stage':name,'status':'ok','result':value}); return value
        except Exception as exc:
            result['steps'].append({'stage':name,'status':'failed','error_type':type(exc).__name__}); return None
    with worker_lock(DATA):
        if live:
            from workflow import collect,enrich
            from prefilter import run as prefilter
            from batch_screen import run as screen,apply_saved
            # Recover only saved material, without retrying unknown billable requests.
            for req in db.execute("SELECT id,response_path FROM ai_requests WHERE status NOT IN ('applied','acknowledged')").fetchall():
                if artifact_path(req['response_path']).is_file(): step('recover:'+req['id'],lambda req=req:apply_saved(db,req['id']))
            for seed in (only or registry()) if discover else []:
                if seed not in registry(): raise ValueError('Unknown journal')
                if source_cycle(seed)!=kind:continue
                step('collect:'+seed,lambda seed=seed:collect(db,[seed],**({'start':window[0],'end':window[1]} if window else {})))
        processing_end=(closed_period(day) or ('',day.isoformat()))[1]
        ids={r['id'] for r in db.execute('SELECT id,journal,data FROM papers WHERE cohort=?',(cohort,))
             if source_cycle(r['journal'])==kind and ((publication_day(json.loads(r['data'])) or '')<=processing_end if not window else window[0]<=(publication_day(json.loads(r['data'])) or '')<=window[1])}
        if live:
            step('type_prefilter',lambda:prefilter(db,cohort,ids))
            step('refresh_legacy',lambda:refresh_legacy(db,cohort=cohort,kind=kind,ids=ids))
            def paid(name,**kwargs):
                if result['billing_unknown'] or result['spent_tokens']>=max_tokens: return
                before={r[0] for r in db.execute('SELECT id FROM ai_requests')}
                step(name,lambda:screen(db,max_tokens-result['spent_tokens'],group_size=12 if kwargs.get('titles_only') else 3,max_groups=max_groups,
                                       cycle_id=cycle_id,skip_blocked=True,cache=AI_CACHE,cohort=cohort,ids=ids,**kwargs))
                for req in db.execute('SELECT id,detail,usage_json FROM ai_requests'):
                    if req['id'] in before or json.loads(req['detail']).get('cache_reused'): continue
                    usage=json.loads(req['usage_json'] or 'null') or {}
                    if type(usage.get('total_tokens')) is int and usage['total_tokens']>=0: result['spent_tokens']+=usage['total_tokens']
                    else: result['billing_unknown']=True
            paid('title_screen',titles_only=True)
            step('enrich',lambda:enrich(db,10,cohort=cohort,ids=ids))
            paid('abstract_screen')
            from quality import prepare
            needs_checks={r['paper_id'] for r in inspect(db,day,cohort=cohort,kind=kind,ids=ids) if r['reason']=='verification_missing_or_stale'}
            qa=step('prepare_verify',lambda:prepare(db,'verify',day.isoformat(),count=50,only_ids=needs_checks,cohort=cohort))
            if qa: paid('verify',quality_batch=qa['batch_id'])
            audit=step('prepare_audit',lambda:prepare(db,'audit',day.isoformat(),count=12,only_ids=ids,cohort=cohort))
            if audit: paid('audit',quality_batch=audit['batch_id'])
        result['admission']=inspect(db,day,cohort=cohort,kind=kind,ids=ids)
        result['publication']=step('publish',lambda:publish_ready(db,day,output,window=window,cohort=cohort,kind=kind,ids=ids)) if publish else {'state':'shadow_only'}
        result['execution_status']='partial' if result['billing_unknown'] or any(s['status']=='failed' for s in result['steps']) else 'completed'
        collected=[s for s in result['steps'] if s['stage'].startswith('collect:')]
        result['coverage_status']='not_checked' if not collected else 'catalogue_checked' if all(s['status']=='ok' and s.get('result',{}).get('coverage')=='catalogue_checked' for s in collected) else 'partial'
        with db:
            db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (now(),'cycle',result['execution_status'],json.dumps(result,ensure_ascii=False)))
    return result
