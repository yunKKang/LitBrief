"""Durable editorial workflow. SQLite is private; only published snapshots are exported."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from audit_sources import doi, plain, combined_works

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'outputs/library'
TOPICS = ['产业与基础设施绿色转型', '全球生产网络与环境责任', '国际绿色产品标准', '供应链管理与韧性', '住房与建成环境']


def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def registry():
    return {s['seed_issn']: s for s in json.loads((ROOT / 'config/sources.json').read_text(encoding='utf-8'))}


def archive_inactive_sources(db):
    """Retain inactive-source records outside working queues, with reversible audit metadata."""
    active = registry()
    with db:
        rows = [r for r in db.execute("SELECT id,journal,cohort,state FROM papers WHERE cohort!='inactive_source'")
                if r['journal'] not in active]
        if any(r['state'] == 'screening' for r in rows):
            raise ValueError('An inactive source has an in-flight AI request; finish it before syncing sources')
        if rows:
            db.executemany("UPDATE papers SET cohort='inactive_source',revision=revision+1,updated_at=? WHERE id=?",
                           [(now(), r['id']) for r in rows])
            db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (now(), 'source-retirement', 'completed', json.dumps({'records': [dict(r) for r in rows]})))
    return len(rows)


def connect(path=DATA / 'library.sqlite3'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS papers (
      id INTEGER PRIMARY KEY, journal TEXT NOT NULL, data TEXT NOT NULL,
      state TEXT NOT NULL, result TEXT, topics TEXT NOT NULL DEFAULT '[]',
      first_seen TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS identities (
      identity TEXT PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id));
    CREATE TABLE IF NOT EXISTS observations (
      paper_id INTEGER NOT NULL REFERENCES papers(id), origin TEXT NOT NULL,
      digest TEXT NOT NULL, data TEXT NOT NULL, seen_at TEXT NOT NULL,
      UNIQUE(paper_id, origin, digest));
    CREATE TABLE IF NOT EXISTS reviews (
      id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id),
      at TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS issues (
      id TEXT PRIMARY KEY, end_date TEXT NOT NULL, state TEXT NOT NULL,
      created_at TEXT NOT NULL, published_at TEXT);
    CREATE TABLE IF NOT EXISTS issue_items (
      issue_id TEXT NOT NULL REFERENCES issues(id), paper_id INTEGER NOT NULL REFERENCES papers(id),
      snapshot TEXT NOT NULL, PRIMARY KEY(issue_id,paper_id), UNIQUE(paper_id));
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(papers)')}
    if 'cohort' not in columns:
        db.execute("ALTER TABLE papers ADD COLUMN cohort TEXT NOT NULL DEFAULT 'monitoring'")
        db.commit()
    if 'enrichment_state' not in columns:
        db.execute("ALTER TABLE papers ADD COLUMN enrichment_state TEXT NOT NULL DEFAULT 'not_attempted'")
        db.execute("UPDATE papers SET enrichment_state='available' WHERE length(json_extract(data,'$.abstract'))>0")
        db.commit()
    return db


def publication_day(paper):
    dates = paper.get('dates', {})
    # Issue/cover dates cannot establish a new online publication date.
    value = dates.get('publisher_online') or dates.get('published-online')
    if not value and paper.get('date_role') in {'publisher_date', 'available_online'}:
        value = paper.get('date')
    if not value:
        value = dates.get('published')
    try:
        return date.fromisoformat(value).isoformat() if value and len(value) == 10 else None
    except ValueError:
        return None


def establish_baseline(db, start):
    """One-time historical classification; later discoveries remain in monitoring."""
    date.fromisoformat(start)
    if db.execute("SELECT 1 FROM runs WHERE kind='baseline'").fetchone():
        raise ValueError('Baseline already established; do not reclassify late discoveries')
    count = 0
    with db:
        for row in db.execute('SELECT id,data FROM papers').fetchall():
            day = publication_day(json.loads(row['data']))
            if day and day < start:
                db.execute("UPDATE papers SET cohort='baseline' WHERE id=?", (row['id'],))
                count += 1
            elif not day or day > today().isoformat():
                db.execute("UPDATE papers SET cohort='date_unverified' WHERE id=?", (row['id'],))
        db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                   (now(), 'baseline', 'completed', json.dumps({'monitoring_start': start, 'historical': count, 'unknown_dates': 'date_unverified queue; not excluded'})))
    return count


def safe_url(value):
    try:
        u = urlsplit(str(value or ''))
        if u.scheme not in {'https', 'http'} or not u.hostname or u.username or u.password:
            return None
        return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path, u.query, ''))
    except ValueError:
        return None


def identities(paper):
    keys = []
    if identifier := doi(paper.get('doi')):
        keys.append('doi:' + identifier)
    # Exact publisher URL and PII can join a publisher-only entry to a later DOI.
    for url in paper.get('urls', []):
        if clean := safe_url(url):
            u = urlsplit(clean)
            if u.hostname not in {'doi.org', 'dx.doi.org'}:
                keys.append('url:' + urlunsplit((u.scheme, u.netloc, u.path.rstrip('/'), '', '')))
    for pii in paper.get('piis', []) + ([paper['pii']] if paper.get('pii') else []):
        keys.append('pii:' + str(pii).upper())
    if not keys:
        # Keep unidentifiable discoveries pending, never merge merely similar titles.
        keys.append('unverified:' + hashlib.sha256(json.dumps(paper, sort_keys=True).encode()).hexdigest())
    return list(dict.fromkeys(keys))


def ingest(db, paper, origin):
    paper = dict(paper)
    seed = paper.get('journal_seed_issn')
    source = registry().get(seed)
    if not source:
        return None
    paper['journal_title'], paper['display'] = source['title'], source['display']
    paper['doi'] = doi(paper.get('doi'))
    paper['title'] = plain(paper.get('title'))
    if not paper['title']:
        return None
    if paper.get('abstract') and paper.get('abstract_source'):
        paper['abstract'] = plain(paper['abstract'])
    else:
        paper['abstract'], paper['abstract_source'] = '', None
    keys = identities(paper)
    matches = {r[0] for key in keys for r in db.execute('SELECT paper_id FROM identities WHERE identity=?', (key,))}
    if len(matches) > 1:
        raise ValueError('Conflicting identities require manual reconciliation')
    row = db.execute('SELECT * FROM papers WHERE id=?', (next(iter(matches)),)).fetchone() if matches else None
    if row and row['journal'] != seed:
        raise ValueError('Identity matched a different journal; manual review required')
    timestamp = now()
    if row:
        old = json.loads(row['data'])
        if old.get('doi') and paper.get('doi') and old['doi'] != paper['doi']:
            raise ValueError('Conflicting DOI; refusing to merge')
        merged = dict(old)
        # Missing metadata never erases a previously verified abstract or DOI.
        for k, value in paper.items():
            if value not in (None, '', [], {}):
                if k == 'type' and value == 'journal-article' and old.get('type') not in (None, '', 'journal-article'):
                    continue  # Crossref's broad type must not erase a publisher's precise article type.
                if k in {'title', 'title_source'}:
                    priority = {'Crossref': 0, 'publisher_feed': 1, 'publisher_catalogue': 2}
                    if priority.get(old.get('title_source'), 0) > priority.get(paper.get('title_source'), 0):
                        continue
                if k == 'abstract' and old.get('abstract') and 'publisher' not in str(paper.get('abstract_source', '')).lower():
                    continue
                merged[k] = value
        merged['urls'] = sorted(set(old.get('urls', []) + paper.get('urls', [])))
        merged['dates'] = {**old.get('dates', {}), **{k: v for k,v in paper.get('dates', {}).items() if v}}
        if old.get('online_date_source')=='publisher_article' and paper.get('online_date_source')!='publisher_article':
            merged['dates']['publisher_online']=old.get('dates',{}).get('publisher_online')
        # Do not silently change the source attribution when retaining the old abstract.
        if merged.get('abstract') == old.get('abstract') and old.get('abstract'):
            merged['abstract_source'] = old.get('abstract_source')
        material_changed = any(merged.get(k) != old.get(k) for k in ('title', 'abstract', 'abstract_source', 'type'))
        published = db.execute("SELECT 1 FROM issue_items JOIN issues ON issues.id=issue_id WHERE paper_id=? AND issues.state='published'", (row['id'],)).fetchone()
        if material_changed and published:
            db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                       (timestamp, 'published-metadata-change', 'needs_review', json.dumps({'paper_id': row['id'], 'note': 'Published snapshot unchanged; inspect source correction manually'})))
        if material_changed and not published:
            db.execute("DELETE FROM issue_items WHERE paper_id=? AND issue_id IN (SELECT id FROM issues WHERE state='draft')", (row['id'],))
        state = ('pending' if merged.get('abstract') else 'awaiting_abstract') if material_changed and not published else row['state']
        db.execute('UPDATE papers SET data=?,state=?,result=?,updated_at=?,revision=revision+1 WHERE id=?',
                   (json.dumps(merged, ensure_ascii=False), state, None if material_changed and not published else row['result'], timestamp, row['id']))
        paper_id = row['id']
    else:
        state = 'pending' if paper.get('abstract') else 'awaiting_abstract'
        cur = db.execute('INSERT INTO papers(journal,data,state,first_seen,updated_at) VALUES(?,?,?,?,?)',
                         (seed, json.dumps(paper, ensure_ascii=False), state, timestamp, timestamp))
        paper_id = cur.lastrowid
    for key in keys:
        db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)', (key, paper_id))
    raw = json.dumps(paper, sort_keys=True, ensure_ascii=False)
    current = db.execute('SELECT cohort,data FROM papers WHERE id=?', (paper_id,)).fetchone()
    if json.loads(current['data']).get('abstract'):
        db.execute("UPDATE papers SET enrichment_state='available' WHERE id=?",(paper_id,))
    if current['cohort'] not in {'baseline', 'inactive_source'}:
        day = publication_day(json.loads(current['data']))
        cohort = 'monitoring' if day and day <= today().isoformat() else 'date_unverified'
        baseline = db.execute("SELECT detail FROM runs WHERE kind='baseline' ORDER BY id LIMIT 1").fetchone()
        start = json.loads(baseline[0])['monitoring_start'] if baseline else None
        # A discovery lookback is not an editorial expiry. Late indexing and monthly
        # screening must retain records published since the fixed monitoring start.
        if day and start and day < start:cohort='baseline'
        db.execute('UPDATE papers SET cohort=? WHERE id=?', (cohort,paper_id))
    db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)',
               (paper_id, origin, hashlib.sha256(raw.encode()).hexdigest(), raw, timestamp))
    return paper_id


def import_audit(db, folder):
    folder = Path(folder)
    count = 0
    with db:
        # Independent streams: publisher discoveries are never contingent on Crossref.
        for name in ('candidates.json', 'publisher-pending.json'):
            if (folder / name).exists():
                for paper in json.loads((folder / name).read_text(encoding='utf-8')):
                    count += ingest(db, paper, str(folder / name)) is not None
        for file in (folder / 'journals').glob('*.json'):
            data = json.loads(file.read_text(encoding='utf-8'))
            family = set((data.get('identity') or {}).get('ISSN', []))
            for entry in combined_works(data).values():
                if not family.intersection(entry.get('issns', [])):
                    continue
                count += ingest(db, {**entry, 'journal_seed_issn': data['seed_issn'], 'title_source': 'Crossref',
                                     'abstract_source': 'Crossref' if entry.get('abstract') else None}, str(file)) is not None
        for file in (folder / 'publisher').glob('*.json'):
            data = json.loads(file.read_text(encoding='utf-8'))
            for source in data.get('sources', []):
                for entry in (source.get('parsed') or {}).get('items', []):
                    count += ingest(db, {**entry, 'journal_seed_issn': data['seed_issn'], 'title_source': 'publisher_feed'}, str(file)) is not None
        for file in (folder / 'catalogue').glob('*.json'):
            data = json.loads(file.read_text(encoding='utf-8'))
            for entry in data.get('items', []):
                paper = {**entry, 'journal_seed_issn': data['seed_issn'], 'title_source': 'publisher_catalogue',
                         'urls': [entry['url']] if entry.get('url') else [],
                         'type': entry.get('article_type'), 'dates': {'publisher_online': entry.get('date')}}
                count += ingest(db, paper, str(file)) is not None
        db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)', (now(), 'import', 'completed', json.dumps({'folder': str(folder), 'observations': count})))
    return count


def review(db, paper_id, action, summary, topics, note, revision):
    if action not in {'approve', 'reject', 'requeue'} or any(t not in TOPICS for t in topics):
        raise ValueError('Invalid review action or topic')
    with db:
        row = db.execute('SELECT * FROM papers WHERE id=?', (paper_id,)).fetchone()
        if not row or row['revision'] != revision:
            raise ValueError('This paper changed; reload before reviewing')
        if action in {'approve', 'requeue'} and row['journal'] not in registry():
            raise ValueError('Journal is inactive; its retained records cannot enter review or publication queues')
        if db.execute('SELECT 1 FROM issue_items WHERE paper_id=?', (paper_id,)).fetchone():
            raise ValueError('Paper belongs to an issue; remove draft before editing. Published snapshots are immutable.')
        data = json.loads(row['data'])
        result = json.loads(row['result'] or '{}')
        if action == 'approve':
            if not data.get('abstract') or not data.get('abstract_source') or not summary.strip() or not topics:
                raise ValueError('Approval requires a sourced abstract, summary and at least one topic')
            if not data.get('doi') and not any(safe_url(u) for u in data.get('urls', [])):
                raise ValueError('Approval requires an original article link')
            result.update(summary=summary.strip(), decision='include', basis='仅依据摘要', human_reviewed=True)
        state = {'approve': 'approved', 'reject': 'rejected', 'requeue': 'pending' if data.get('abstract') else 'awaiting_abstract'}[action]
        db.execute('UPDATE papers SET state=?,result=?,topics=?,revision=revision+1,updated_at=? WHERE id=?',
                   (state, json.dumps(result, ensure_ascii=False), json.dumps(topics, ensure_ascii=False), now(), paper_id))
        db.execute('INSERT INTO reviews(paper_id,at,action,note) VALUES(?,?,?,?)', (paper_id, now(), action, note))


def period(day=None):
    cfg = json.loads((ROOT / 'config/runtime.json').read_text(encoding='utf-8'))
    anchor = date.fromisoformat(cfg['issue_anchor'])
    day = day or today()
    start = anchor + timedelta(days=((day - anchor).days // cfg['issue_days']) * cfg['issue_days'])
    return start.isoformat(), (start + timedelta(days=cfg['issue_days'] - 1)).isoformat()


def build_issue(db, day=None):
    start, end = period(day)
    with db:
        existing = db.execute('SELECT state FROM issues WHERE id=?', (start,)).fetchone()
        if existing and existing['state'] == 'published':
            return start  # Daily preparation is idempotent; late papers wait for the next period.
        db.execute("INSERT OR IGNORE INTO issues VALUES(?,?,'draft',?,NULL)", (start, end, now()))
        for row in db.execute("SELECT * FROM papers WHERE state='approved' AND id NOT IN (SELECT paper_id FROM issue_items)").fetchall():
            data, result = json.loads(row['data']), json.loads(row['result'])
            source = registry().get(row['journal'])
            if not source:
                continue
            # Explicit public allowlist: never serialize source abstracts, logs or credentials.
            snapshot = {'id': row['id'], 'title': data['title'], 'doi': data.get('doi'),
                        'title_zh': result.get('title_zh', ''),
                        'journal': source['title'], 'display': source['display'],
                        'summary': result['summary'], 'topics': json.loads(row['topics']),
                        'reason': result.get('reason',''),
                        'basis': '仅依据摘要', 'abstract_source': data.get('abstract_source'),
                        'dates': data.get('dates', {}),
                        'url': ('https://doi.org/' + data['doi']) if data.get('doi') else next((u for u in data.get('urls', []) if safe_url(u)), '')}
            db.execute('INSERT INTO issue_items VALUES(?,?,?)', (start, row['id'], json.dumps(snapshot, ensure_ascii=False)))
    return start


def publish_issue(db, issue_id):
    with db:
        issue = db.execute('SELECT * FROM issues WHERE id=?', (issue_id,)).fetchone()
        if not issue or issue['state'] != 'draft':
            raise ValueError('Only a draft can be published')
        if not db.execute('SELECT 1 FROM issue_items WHERE issue_id=?', (issue_id,)).fetchone():
            raise ValueError('Empty issue stays a draft; never fill a quota')
        db.execute("UPDATE issues SET state='published',published_at=? WHERE id=?", (now(), issue_id))


@contextmanager
def worker_lock(folder=DATA):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / 'worker.lock'
    try:
        stream = lock.open('x', encoding='utf-8')
    except FileExistsError:
        raise RuntimeError('Another worker may be running; inspect worker.lock before manual recovery') from None
    try:
        import os
        with stream:
            stream.write(json.dumps({'pid': os.getpid(), 'started': now()}))
        yield
    finally:
        lock.unlink()
