"""CLI for collection, import, bounded AI trials, issue preparation and static export."""
import argparse
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import audit_sources as audit
from library import DATA, ROOT, connect, import_audit, ingest, now, today, worker_lock, build_issue, registry, archive_inactive_sources


def collect(db, only=None, *, start=None, end=None, feeds_only=False):
    cfg = json.loads((ROOT / 'config/runtime.json').read_text(encoding='utf-8'))
    end = date.fromisoformat(end) if isinstance(end,str) else end or today()
    start = date.fromisoformat(start) if isinstance(start,str) else start or end - timedelta(days=cfg['lookback_days'])
    if start>end or end>today():raise ValueError('Invalid collection window')
    folder = DATA / 'collections' / now().replace(':', '').replace('+', '_')
    old_output = audit.OUTPUT
    with worker_lock(DATA):
        audit.OUTPUT = folder
        args = SimpleNamespace(start=start.isoformat(), end=end.isoformat(), only=only, retry_failed=False)
        try:
            # Persist publisher discoveries before requesting Crossref.
            audit.run_publisher(args)
            import_audit(db, folder)
            if not feeds_only:
                from publisher_collect import collect as publisher_html
                publisher_html(db, folder / 'publisher-html', only)
                # Traverse each year separately; the parser's single-year invariant is preserved.
                for year in range(start.year, end.year + 1):
                    year_folder = folder / str(year)
                    audit.OUTPUT = year_folder
                    args.start = max(start, date(year, 1, 1)).isoformat()
                    args.end = min(end, date(year, 12, 31)).isoformat()
                    audit.run_catalogue(args)
                    import_audit(db, year_folder)
                audit.OUTPUT = folder
                args.start, args.end = start.isoformat(), end.isoformat()
                audit.run_crossref(args)
                import_audit(db, folder)
            from prefilter import run as prefilter
            prefilter(db)
            from source_coverage import summarize
            source_status=summarize(db,folder,args.start,args.end,only)
            detail = {'folder': str(folder), 'start': args.start, 'end': args.end,
                      'scope': only or 'all', 'coverage': 'catalogue_checked' if source_status and all(s['coverage']=='catalogue_checked' for s in source_status) else 'partial',
                      'sources':source_status}
            with db:
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)', (now(), 'collect', detail['coverage'], json.dumps(detail)))
            return detail
        finally:
            audit.OUTPUT = old_output


def status(db):
    counts = {r[0]: r[1] for r in db.execute('SELECT state,count(*) FROM papers GROUP BY state')}
    cohorts = {}
    for r in db.execute('SELECT cohort,state,count(*) FROM papers GROUP BY cohort,state'):
        cohorts.setdefault(r[0], {})[r[1]] = r[2]
    size = sum(p.stat().st_size for p in (ROOT / 'outputs').rglob('*') if p.is_file())
    cfg = json.loads((ROOT / 'config/runtime.json').read_text(encoding='utf-8'))
    return {'papers': counts, 'papers_by_cohort': cohorts,
            'unresolved_ai_requests': [dict(r) for r in db.execute("SELECT id,mode,status,usage_json,detail FROM ai_requests WHERE status NOT IN ('applied','acknowledged') ORDER BY created_at")],
            'issues': [dict(r) for r in db.execute('SELECT * FROM issues ORDER BY id DESC')],
            'abstract_provider_holds': [dict(r) for r in db.execute('SELECT * FROM enrichment_holds ORDER BY provider')],
            'abstract_queue': [dict(r) for r in db.execute("SELECT enrichment_state,count(*) AS count FROM papers WHERE state='awaiting_abstract' AND cohort='monitoring' GROUP BY enrichment_state")],
            'storage_bytes': size, 'storage_warning': size >= cfg['storage_warning_gb'] * 1024 ** 3}


def enrich(db, max_items, **kwargs):
    from enrichment import run
    return run(db, max_items, **kwargs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=DATA / 'library.sqlite3')
    sub = p.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import-audit')
    imp.add_argument('folder', type=Path)
    item = sub.add_parser('import-paper')
    item.add_argument('file', type=Path)
    col = sub.add_parser('collect')
    col.add_argument('--only', nargs='+')
    col.add_argument('--feeds-only',action='store_true',help='Lightweight discovery without catalogue or Crossref traversal')
    col.add_argument('--start')
    col.add_argument('--end')
    en = sub.add_parser('enrich')
    en.add_argument('--max-items', required=True, type=int)
    recovery = sub.add_parser('resume-enrichment', help='After inspection, clear selected holds; no network or AI')
    recovery.add_argument('--ids', nargs='+', type=int, default=[])
    recovery.add_argument('--providers', nargs='+', choices=['nature', 'openalex', 'europepmc'], default=[])
    ai = sub.add_parser('screen')
    ai.add_argument('--max-calls', type=int, required=True)
    ai.add_argument('--max-tokens', type=int, required=True)
    ai.add_argument('--ids', nargs='+', type=int, help='Optional explicit calibration samples; other candidates remain queued')
    sub.add_parser('status')
    sub.add_parser('usage',help='Reconcile provider-reported usage across the request ledger and legacy logs')
    sub.add_parser('sync-sources', help='Archive inactive sources without deleting records or calling AI')
    sub.add_parser('prepare-issue')
    exp = sub.add_parser('export')
    exp.add_argument('--output', type=Path, default=ROOT / 'outputs/public')
    sub.add_parser('backup')
    sub.add_parser('normalize-paths', help='Normalize existing evidence paths on the original host before migration; back up first')
    sub.add_parser('cleanup-plan',help='Create an exact retention preview; no deletion')
    clean=sub.add_parser('cleanup-apply',help='Apply only the unchanged explicitly confirmed preview')
    clean.add_argument('--plan',required=True,type=Path)
    clean.add_argument('--confirm',required=True,help='Full SHA-256 plan ID reviewed by the operator')
    loop = sub.add_parser('run-cycle', help='Offline shadow by default; explicit live processing and separate local publication')
    loop.add_argument('--live', action='store_true', help='Collect and run paid AI under a shared token threshold')
    loop.add_argument('--publish', action='store_true', help='Requires publication_mode=automatic; confirms closed issues and exports locally')
    loop.add_argument('--max-tokens', type=int, default=0)
    loop.add_argument('--max-groups', type=int, default=3)
    loop.add_argument('--collect-only', dest='only', nargs='+', help='Limit discovery to these ISSNs; existing candidate processing remains global')
    loop.add_argument('--output', type=Path, default=ROOT/'outputs/public')
    loop.add_argument('--window-start')
    loop.add_argument('--window-end')
    loop.add_argument('--kind', choices=['weekly','monthly'], default='weekly')
    loop.add_argument('--cohort', choices=['monitoring','baseline'], default='monitoring')
    loop.add_argument('--skip-discovery', action='store_true', help='Process the scoped existing queue without rediscovering sources')
    qa = sub.add_parser('prepare-quality', help='Prepare reproducible private samples; no AI calls')
    qa.add_argument('--mode',choices=['audit','verify'],required=True)
    qa.add_argument('--seed',required=True)
    qa.add_argument('--count',type=int,default=12)
    qa.add_argument('--targeted',type=int,nargs='*',default=[])
    qr = sub.add_parser('quality-report')
    qr.add_argument('--batch-id',required=True)
    qc = sub.add_parser('check-quality',help='Explicit bounded agent checking; no approval or publication')
    qc.add_argument('--batch-id',required=True)
    qc.add_argument('--max-tokens',required=True,type=int)
    qc.add_argument('--max-groups',type=int,default=1)
    qc.add_argument('--group-size',type=int,default=3)
    recover = sub.add_parser('recover-ai', help='Validate and apply a saved batch response without networking')
    recover.add_argument('--request-id', required=True)
    ack = sub.add_parser('acknowledge-ai', help='Record manual billing verification before any explicit requeue')
    ack.add_argument('--request-id', required=True)
    ack.add_argument('--note', required=True)
    args = p.parse_args()
    with closing(connect(args.db)) as db:
        if args.command == 'import-audit':
            result = {'observations': import_audit(db, args.folder)}
        elif args.command == 'import-paper':
            with db:
                result = {'paper_id': ingest(db, json.loads(args.file.read_text(encoding='utf-8-sig')), str(args.file))}
        elif args.command == 'collect':
            result = collect(db, args.only,start=args.start,end=args.end,feeds_only=args.feeds_only)
        elif args.command == 'enrich':
            result = enrich(db, args.max_items)
        elif args.command == 'resume-enrichment':
            from enrichment import resume
            result = resume(db, args.ids, args.providers)
        elif args.command == 'screen':
            from batch_screen import run
            result = run(db, args.max_tokens, group_size=1, max_groups=args.max_calls, ids=args.ids)
        elif args.command == 'recover-ai':
            from batch_screen import recover
            result = recover(db, args.request_id)
        elif args.command == 'cleanup-plan':
            from retention import build_plan,save_plan
            plan=build_plan(db);path=save_plan(plan)
            result={'plan_file':str(path),'plan_id':plan['id'],'files':len(plan['files']),
                    'file_bytes':plan['file_bytes'],'papers':len(plan['papers']),'paper_logical_bytes':plan['paper_logical_bytes'],'notes':plan['notes']}
        elif args.command == 'cleanup-apply':
            from retention import apply_plan
            result=apply_plan(db,json.loads(args.plan.read_text('utf-8')),args.confirm)
        elif args.command == 'run-cycle':
            from automation import cycle
            if bool(args.window_start)!=bool(args.window_end):p.error('Both window bounds are required')
            result=cycle(db,live=args.live,publish=args.publish,max_tokens=args.max_tokens,
                         max_groups=args.max_groups,only=args.only,output=args.output,
                         window=(args.window_start,args.window_end) if args.window_start else None,
                         kind=args.kind,cohort=args.cohort,discover=not args.skip_discovery)
        elif args.command == 'prepare-quality':
            from quality import prepare
            result = prepare(db,args.mode,args.seed,args.count,args.targeted)
        elif args.command == 'quality-report':
            from quality import report
            result = report(db,args.batch_id)
        elif args.command == 'check-quality':
            from batch_screen import run
            result = run(db,args.max_tokens,group_size=args.group_size,max_groups=args.max_groups,quality_batch=args.batch_id)
        elif args.command == 'acknowledge-ai':
            from batch_screen import acknowledge
            result = acknowledge(db, args.request_id, args.note)
        elif args.command == 'sync-sources':
            with worker_lock(DATA):
                result = {'archived_records': archive_inactive_sources(db)}
        elif args.command == 'prepare-issue':
            result = {'draft_issue': build_issue(db)}
        elif args.command == 'export':
            from webapp import export_site
            release = export_site(db, args.output)
            result = {'public_directory': str(release), 'note': 'Serve or upload only this complete release, not its parent container.'}
        elif args.command == 'usage':
            from usage_report import report
            result=report(db)
        elif args.command == 'normalize-paths':
            from library import normalize_artifact_paths
            with worker_lock(DATA):
                result = normalize_artifact_paths(db)
        elif args.command == 'backup':
            backup = DATA / 'backups' / (now().replace(':', '').replace('+', '_') + '.sqlite3')
            backup.parent.mkdir(parents=True, exist_ok=True)
            import sqlite3
            with closing(sqlite3.connect(backup)) as dest:
                db.backup(dest)
            result = {'backup': str(backup)}
        else:
            result = status(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('execution_status')=='partial':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
