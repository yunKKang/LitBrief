"""CLI for collection, import, bounded AI trials, issue preparation and static export."""
import argparse
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import audit_sources as audit
from library import DATA, ROOT, connect, import_audit, ingest, now, today, worker_lock, build_issue, registry, archive_inactive_sources


def collect(db, only=None):
    cfg = json.loads((ROOT / 'config/runtime.json').read_text(encoding='utf-8'))
    end = today()
    start = end - timedelta(days=cfg['lookback_days'])
    folder = DATA / 'collections' / now().replace(':', '').replace('+', '_')
    old_output = audit.OUTPUT
    with worker_lock(DATA):
        audit.OUTPUT = folder
        args = SimpleNamespace(start=start.isoformat(), end=end.isoformat(), only=only, retry_failed=False)
        try:
            # Persist publisher discoveries before requesting Crossref.
            audit.run_publisher(args)
            import_audit(db, folder)
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
            detail = {'folder': str(folder), 'start': args.start, 'end': args.end,
                      'scope': only or 'all', 'coverage': 'partial: non-Nature full catalogues are not automated'}
            with db:
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)', (now(), 'collect', 'partial', json.dumps(detail)))
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


def enrich(db, max_items):
    from enrichment import run
    return run(db, max_items)


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
    sub.add_parser('sync-sources', help='Archive inactive sources without deleting records or calling AI')
    sub.add_parser('prepare-issue')
    exp = sub.add_parser('export')
    exp.add_argument('--output', type=Path, default=ROOT / 'outputs/public')
    sub.add_parser('backup')
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
            result = collect(db, args.only)
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


if __name__ == '__main__':
    main()
