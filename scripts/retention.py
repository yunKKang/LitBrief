"""Preview-first retention. Exact manifests, safe roots, unchanged evidence and explicit execution.

Published/draft papers, human feedback, sampled calibration and unresolved calls are protected.
Shared or legacy evidence that cannot be safely attributed is retained, never guessed away.
"""
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from library import ROOT, worker_lock, now, artifact_path

TABLES = ('papers','identities','observations','reviews','issues','issue_items','runs',
          'enrichment_schedule','enrichment_holds','ai_requests','quality_batches','quality_jobs','feedback','issue_notices')
DATA_KEYS = {'journal_seed_issn','journal_title','display','doi','title','type','dates','urls','pii','piis',
             'date','date_role','title_source','online_date_source','retention_compacted'}
RESULT_KEYS = {'decision','reason','basis','prefilter','title_screened','policy_hash','request_id'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def config(): return json.loads((ROOT/'config/retention.json').read_text('utf-8'))


def safe_root(value=None):
    path=Path(value or ROOT/'outputs').absolute()
    for parent in [path,*path.parents]:
        if parent.exists():
            info=parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                raise ValueError('Retention root cannot traverse a symlink or junction')
    return path.resolve()


def db_digest(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables!=set(TABLES):raise ValueError('Database schema changed; review retention coverage before cleanup')
    h=hashlib.sha256()
    for table in TABLES:
        h.update(table.encode())
        for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid'):
            h.update(json.dumps(dict(row),sort_keys=True,ensure_ascii=False).encode());h.update(b'\n')
    return h.hexdigest()


def checked_path(root, relative):
    root=Path(root).resolve();rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts: raise ValueError('Retention path must stay within outputs')
    path=root/rel
    for part in [path,*path.parents]:
        if part==root: break
        if part.exists() or part.is_symlink():
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                raise ValueError('Retention refuses symlinks and junctions')
    if not path.resolve().is_relative_to(root): raise ValueError('Retention escaped outputs')
    return path


def files_under(folder):
    if not folder.is_dir(): return
    for entry in os.scandir(folder):
        p=Path(entry.path);info=p.lstat()
        if entry.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):continue
        if entry.is_dir(follow_symlinks=False):yield from files_under(p)
        elif entry.is_file(follow_symlinks=False):yield p


def file_record(root,path,kind):
    path=safe_root(path)  # Normalize Windows short paths only after rejecting reparse points.
    relative=path.relative_to(root).as_posix();path=checked_path(root,relative)
    if not path.is_file():raise ValueError('Expected ordinary retention file')
    before=path.stat()
    data=path.read_bytes()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('File changed while preparing its checksum')
    return {'path':relative,'kind':kind,'bytes':len(data),'mtime_ns':after.st_mtime_ns,'sha256':hashlib.sha256(data).hexdigest()}


def slim_data(value):
    data={k:v for k,v in value.items() if k in DATA_KEYS}
    data.update(abstract='',abstract_source=None,retention_compacted=True)
    return data


def slim_result(value): return {k:v for k,v in (value or {}).items() if k in RESULT_KEYS}


def expired_candidates(db, cutoff, root):
    candidates={r['id'] for r in db.execute("SELECT id,data FROM papers WHERE state IN ('ai_excluded','type_excluded','rejected') AND updated_at<?",(cutoff,))
                if not json.loads(r['data']).get('retention_compacted')}
    protected=set()
    for table in ('issue_items','reviews','feedback','quality_jobs'):
        protected.update(r[0] for r in db.execute('SELECT DISTINCT paper_id FROM '+table))
    candidates-=protected
    requests=[]
    known_files=set()
    for r in db.execute('SELECT * FROM ai_requests'):
        inputs=json.loads(r['inputs']);ids={p['id'] for p in inputs}
        requests.append((r,ids))
        response=artifact_path(r['response_path'], root.parent).resolve()
        known_files.update({response,(root/'ai-trial'/(r['fingerprint']+'.group.json')).resolve(),
                            (root/'ai-trial'/(r['fingerprint']+'.response.json')).resolve()})
        if r['status'] not in {'applied','acknowledged'} or r['updated_at']>=cutoff:
            candidates-=ids
        if not response.is_relative_to(root/'ai-trial'):
            candidates-=ids
    # Old unregistered model responses may contain another copy of any candidate.
    # Keep database detail until those files have been attributed or explicitly retired.
    legacy=any(p.resolve() not in known_files for p in (root/'ai-trial').glob('*.json'))
    if legacy:candidates.clear()
    # A shared request can only be retired when all its members can be compacted.
    changed=True
    while changed:
        before=set(candidates)
        for _,ids in requests:
            if ids&candidates and not ids<=candidates:candidates-=ids
        changed=before!=candidates
    # Legacy normalized input files may contain copies not covered by this plan.
    for row in db.execute('SELECT paper_id,origin FROM observations'):
        if row['paper_id'] in candidates and Path(row['origin']).is_file():
            candidates.remove(row['paper_id'])
    changed=True
    while changed:
        before=set(candidates)
        for _,ids in requests:
            if ids&candidates and not ids<=candidates:candidates-=ids
        changed=before!=candidates
    compacted={r['id'] for r in db.execute('SELECT id,data FROM papers') if json.loads(r['data']).get('retention_compacted')}
    retired=[dict(r) for r,ids in requests if ids and (ids<=candidates or
             (ids<=compacted and not ids&protected and r['status'] in {'applied','acknowledged'} and r['updated_at']<cutoff))]
    return candidates,retired


def build_plan(db, root=None, clock=None):
    root=safe_root(root);clock=clock or datetime.now(timezone.utc);cfg=config()
    # Snapshot the database consistently; no record is altered while planning.
    with db:
        db.execute('BEGIN')
        stamp=db_digest(db)
        ids,requests=expired_candidates(db,(clock-timedelta(days=cfg['excluded_days'])).isoformat(),root)
        paper_rows=[dict(r) for r in db.execute('SELECT * FROM papers ORDER BY id') if r['id'] in ids]
        estimate=sum(len(r['data'].encode())+len((r['result'] or '').encode())-
                     len(json.dumps(slim_data(json.loads(r['data'])),ensure_ascii=False).encode())-
                     len(json.dumps(slim_result(json.loads(r['result'] or '{}')),ensure_ascii=False).encode()) for r in paper_rows)
        exports=[json.loads(r[0]) for r in db.execute("SELECT detail FROM runs WHERE kind='export' AND status='ready' ORDER BY id DESC")]
        unresolved={artifact_path(r[0], root.parent).resolve() for r in db.execute("SELECT response_path FROM ai_requests WHERE status NOT IN ('applied','acknowledged')")}
        pending_material=db.execute("SELECT 1 FROM papers WHERE cohort='monitoring' AND state IN ('awaiting_abstract','needs_material','ai_error','screening') LIMIT 1").fetchone() is not None
    entries={};notes=[]
    def add(path,kind):
        record=file_record(root,path,kind)
        if path.resolve() not in unresolved:entries[record['path']]=record
    # Rotate dated backups, retaining all recent recovery points plus daily/weekly representatives.
    backups=[]
    folder=root/'library/backups'
    if folder.exists():checked_path(root,'library/backups')
    for p in folder.glob('*.sqlite3'):
        checked_path(root,p.relative_to(root))
        if p.with_name(p.name+'-wal').exists():continue
        with p.open('rb') as stream:
            if stream.read(16)!=b'SQLite format 3\x00':continue
        backups.append((p,datetime.fromtimestamp(p.stat().st_mtime,timezone.utc)))
    backups.sort(key=lambda x:x[1],reverse=True);keep=set();days=set();weeks=set()
    for p,at in backups:
        if at>=clock-timedelta(days=cfg['backup_min_days']):keep.add(p)
        local=at.astimezone(timezone(timedelta(hours=8)))
        if local.date() not in days and len(days)<cfg['backup_daily']:keep.add(p);days.add(local.date())
        week=local.isocalendar()[:2]
        if week not in weeks and len(weeks)<cfg['backup_weekly']:keep.add(p);weeks.add(week)
    for p,_ in backups:
        if p not in keep:add(p,'backup')
    # Only successful HTTP cache pairs, not arbitrary diagnostics or normalized corpus files.
    cache_roots=[p/'raw' for p in root.glob('source-audit-*') if p.is_dir()]
    for folder in (root/'library/collections',root/'library/enrichment'):
        if folder.name=='enrichment' and pending_material:
            notes.append('补材料队列未完成，暂保留enrichment缓存。');continue
        if folder.exists():
            checked_path(root,folder.relative_to(root))
            cache_roots.extend(p.parent for p in files_under(folder) if p.suffix=='.body')
    cutoff=(clock-timedelta(days=cfg['cache_days'])).timestamp()
    for folder in set(cache_roots):
        checked_path(root,folder.relative_to(root))
        for meta in folder.glob('*.json'):
            body=meta.with_suffix('.body')
            if not body.is_file() or max(meta.stat().st_mtime,body.stat().st_mtime)>=cutoff:continue
            checked_path(root,meta.relative_to(root));checked_path(root,body.relative_to(root))
            try:record=json.loads(meta.read_text('utf-8'))
            except (ValueError,UnicodeError):continue
            if record.get('ok') is True and record.get('sha256')==hashlib.sha256(body.read_bytes()).hexdigest():
                add(meta,'http_cache');add(body,'http_cache')
    # Only known complete exports; latest must validate before any older release is proposed.
    marker=root/'public/latest.json'
    if marker.exists():
        checked_path(root,'public/latest.json')
        try:
            latest=json.loads(marker.read_text('utf-8'))
            current=checked_path(root,'public/'+latest['directory'])
            from webapp import validate_release
            if validate_release(current)!=latest['files']:raise ValueError('Current release changed')
            release_dirs=[]
            for export in exports:
                path=Path(export['directory']).resolve()
                if path.parent==root/'public/releases' and path not in release_dirs:
                    checked_path(root,path.relative_to(root));release_dirs.append(path)
            retained={current}|set([p for p in release_dirs if p!=current][:cfg['release_previous']])
            for folder in release_dirs:
                if folder not in retained:
                    for p in files_under(folder):add(p,'release')
        except (ValueError,KeyError,OSError):notes.append('当前公开版本未通过校验，所有release均保留。')
    # Retire only exclusively expired, terminal request files. Old mixed caches stay protected.
    for req in requests:
        candidates=[artifact_path(req['response_path'], root.parent),root/'ai-trial'/(req['fingerprint']+'.group.json'),root/'ai-trial'/(req['fingerprint']+'.response.json')]
        for p in candidates:
            if p.is_file() and p.resolve().is_relative_to(root/'ai-trial'):add(p,'retired_ai')
    database=str(Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve())
    value={'version':1,'root':str(root),'database':database,'created_at':clock.isoformat(),'policy':cfg,'database_digest':stamp,
           'papers':[{'id':r['id'],'revision':r['revision'],'title':json.loads(r['data']).get('title'),'state':r['state']} for r in paper_rows],
           'requests':[r['id'] for r in requests], 'files':sorted(entries.values(),key=lambda r:r['path']),
           'file_bytes':sum(r['bytes'] for r in entries.values()),'paper_logical_bytes':max(0,estimate),
           'notes':notes+['人工反馈、抽检样本、期次与未解决调用保留。共用证据或未覆盖的旧文件副本会阻止该文章精简。',
                          '数据库数值是主记录的逻辑估算；SQLite空闲页会复用，文件不保证立即缩小。旧备份按轮转逐步到期。']}
    value['id']=digest(value)
    return value


def save_plan(plan):
    root=Path(plan['root']);folder=checked_path(root,'library/retention');folder.mkdir(parents=True,exist_ok=True)
    if len(plan['id'])!=64 or any(c not in '0123456789abcdef' for c in plan['id']):raise ValueError('Invalid plan identifier')
    path=checked_path(root,'library/retention/'+plan['id']+'.json')
    path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


def compact(db, ids, requests):
    for pid in ids:
        row=db.execute('SELECT data,result FROM papers WHERE id=?',(pid,)).fetchone()
        db.execute('UPDATE papers SET data=?,result=?,revision=revision+1 WHERE id=?',
                   (json.dumps(slim_data(json.loads(row['data'])),ensure_ascii=False),json.dumps(slim_result(json.loads(row['result'] or '{}')),ensure_ascii=False),pid))
        for obs in db.execute('SELECT rowid,data,digest FROM observations WHERE paper_id=?',(pid,)).fetchall():
            data=slim_data(json.loads(obs['data']));data['original_observation_sha256']=obs['digest']
            raw=json.dumps(data,ensure_ascii=False,sort_keys=True)
            db.execute('UPDATE observations SET data=?,digest=? WHERE rowid=?',(raw,hashlib.sha256(raw.encode()).hexdigest(),obs['rowid']))
        for run in db.execute("SELECT id,detail FROM runs WHERE kind IN ('material-change','policy-refresh') AND json_extract(detail,'$.paper_id')=?",(pid,)).fetchall():
            detail=json.loads(run['detail'])
            if 'previous_data' in detail:detail['previous_data']=slim_data(detail['previous_data'])
            if 'previous_result' in detail:detail['previous_result']=slim_result(detail['previous_result'])
            detail['retention_compacted']=True
            db.execute('UPDATE runs SET detail=? WHERE id=?',(json.dumps(detail,ensure_ascii=False),run['id']))
    for request_id in requests:
        req=db.execute('SELECT * FROM ai_requests WHERE id=?',(request_id,)).fetchone()
        if json.loads(req['request_json']).get('retention_compacted'):continue
        inputs=json.loads(req['inputs'])
        for row in inputs:
            row['data']=json.dumps(slim_data(json.loads(row['data'])),ensure_ascii=False)
            row['result']=json.dumps(slim_result(json.loads(row.get('result') or '{}')),ensure_ascii=False)
        payload=json.loads(req['request_json'])
        compact_payload={k:payload[k] for k in ('model','reasoning','service_tier') if k in payload}
        compact_payload.update(retention_compacted=True,original_request_digest=digest(payload))
        db.execute('UPDATE ai_requests SET inputs=?,request_json=? WHERE id=?',
                   (json.dumps(inputs,ensure_ascii=False),json.dumps(compact_payload),request_id))


def apply_plan(db, plan, confirmation, root=None):
    root=safe_root(root)
    identifier=plan.get('id');content={k:v for k,v in plan.items() if k!='id'}
    if identifier!=confirmation or digest(content)!=identifier or plan['root']!=str(root):
        raise ValueError('Exact reviewed plan and confirmation digest required')
    age=datetime.now(timezone.utc)-datetime.fromisoformat(plan['created_at'])
    if age>timedelta(days=1) or age<timedelta(minutes=-5):
        raise ValueError('Plan expired; generate a new preview')
    release_lock=worker_lock(root/'public') if any(f['kind']=='release' for f in plan['files']) else nullcontext()
    with worker_lock(root/'library'),release_lock:
        # Recompute scope with the same cutoff, then demand an unchanged candidate set.
        current=build_plan(db,root,datetime.fromisoformat(plan['created_at']))
        if current!=plan:raise ValueError('Data, files or policy changed; regenerate the preview')
        backup=None
        if plan['papers']:
            backup=checked_path(root,'library/backups/pre-retention-'+identifier[:16]+'-'+uuid4().hex[:8]+'.sqlite3')
            backup.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(backup)) as dest:db.backup(dest)
        with db:
            db.execute('BEGIN IMMEDIATE')
            if db_digest(db)!=plan['database_digest']:raise ValueError('Database changed during preparation')
            compact(db,[p['id'] for p in plan['papers']],plan['requests'])
            action=db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (now(),'retention','database_compacted',json.dumps({'plan_id':identifier,'paper_ids':[p['id'] for p in plan['papers']]})))
        deleted=[];failed=[]
        for item in plan['files']:
            try:
                path=checked_path(root,item['path'])
                if file_record(root,path,item['kind'])!=item:raise ValueError('File changed after preview')
                path.unlink();deleted.append(item['path'])
            except (OSError,ValueError):failed.append(item['path'])
        with db:
            db.execute('UPDATE runs SET status=?,detail=? WHERE id=?',
                       ('partial' if failed else 'completed',json.dumps({'plan_id':identifier,'papers_compacted':len(plan['papers']),
                        'files_deleted':len(deleted),'failed_files':failed}),action.lastrowid))
        return {'plan_id':identifier,'papers_compacted':len(plan['papers']),'files_deleted':len(deleted),
                'failed_files':failed,'backup':str(backup) if backup else None,
                'note':'If files failed, make a new preview. No directory, identity, feedback or published snapshot was deleted.'}
