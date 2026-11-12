"""CLI for collection, import, bounded AI trials, issue preparation and static export."""
import argparse
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import audit_sources as audit
from library import DATA, ROOT, connect, import_audit, ingest, now, today, worker_lock, build_issue, registry, archive_inactive_sources
from screen_paper import screen, settings


def screen_batch(db, max_calls, max_tokens, cache=ROOT / 'outputs/ai-trial', runner=screen, ids=None):
    if max_calls < 1 or max_tokens < 1:
        raise ValueError('A positive per-run call limit and token stop threshold are required')
    config = settings()
    completed = 0
    spent = 0
    with worker_lock(DATA):
        rows = db.execute("SELECT * FROM papers WHERE state='pending' ORDER BY id").fetchall()
        active = registry()
        rows = [row for row in rows if row['journal'] in active]
        if ids:
            rows = [row for row in rows if row['id'] in ids]
        for row in rows:
            if completed >= max_calls or spent >= max_tokens:
                break
            # Persist claim before networking: a crash must not trigger an automatic paid retry.
            with db:
                changed = db.execute("UPDATE papers SET state='screening' WHERE id=? AND revision=? AND state='pending'", (row['id'], row['revision'])).rowcount
            if not changed:
                continue
            log = Path(cache) / 'usage.jsonl'
            offset = log.stat().st_size if log.exists() else 0
            try:
                result = runner(json.loads(row['data']), config, Path(cache))
                with db:
                    db.execute("UPDATE papers SET state='awaiting_review',result=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND state='screening'",
                               (json.dumps(result, ensure_ascii=False), now(), row['id'], row['revision']))
                completed += 1
            except Exception as exc:
                with db:
                    db.execute("UPDATE papers SET state='ai_error',revision=revision+1 WHERE id=? AND revision=? AND state='screening'", (row['id'], row['revision']))
                    db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                               (now(), 'screen', 'failed', json.dumps({'paper_id': row['id'], 'error_type': type(exc).__name__})))
                raise RuntimeError(f'AI failed for paper {row["id"]}; inspect provider and explicitly requeue. No retry.') from None
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(offset)
                    for line in stream:
                        event = json.loads(line)
                        usage = event.get('usage') or {}
                        if not isinstance(usage.get('total_tokens'), int):
                            raise RuntimeError('Provider omitted usage; stop this batch for budget review')
                        spent += usage['total_tokens']
        with db:
            db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (now(), 'screen', 'completed', json.dumps({'papers': completed, 'tokens': spent})))
    return {'processed': completed, 'new_tokens': spent,
            'note': 'Token threshold is checked between requests; one request may exceed remaining tokens.'}


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
            result = screen_batch(db, args.max_calls, args.max_tokens, ids=args.ids)
        elif args.command == 'sync-sources':
            with worker_lock(DATA):
                result = {'archived_records': archive_inactive_sources(db)}
        elif args.command == 'prepare-issue':
            result = {'draft_issue': build_issue(db)}
        elif args.command == 'export':
            from webapp import export_site
            export_site(db, args.output)
            result = {'public_directory': str(args.output)}
        elif args.command == 'backup':
            backup = DATA / 'backups' / (now().replace(':', '').replace('+', '_') + '.sqlite3')
            backup.parent.mkdir(parents=True, exist_ok=True)
            import sqlite3
            with sqlite3.connect(backup) as dest:
                db.backup(dest)
            result = {'backup': str(backup)}
        else:
            result = status(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
