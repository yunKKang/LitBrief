"""Single public-abstract queue: exact identity, bounded retries and explicit failures.

No API credentials, external AI or browser dependency. Nature uses its verified Abstract
section; OpenAlex and Europe PMC supplement missing material. Restricted publisher APIs
remain explicit access probes until their permissions and coverage are verified.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from urllib.parse import urlencode, quote

from audit_sources import HTTP, doi, plain
from library import DATA, ROOT, ingest, now, registry, worker_lock
from probe_abstracts import AbstractSection, reconstruct

PROVIDERS = ('nature', 'openalex', 'europepmc')


def parse_material(provider, body, identifier):
    try:
        if provider == 'nature':
            parser = AbstractSection()
            parser.feed(body.decode('utf-8', errors='replace'))
            if parser.citation_doi != identifier:
                return {'status': 'content_mismatch'}
            abstract = plain(''.join(parser.text))
        elif provider == 'openalex':
            data = json.loads(body)
            if doi(data.get('doi')) != identifier:
                return {'status': 'content_mismatch'}
            index = data.get('abstract_inverted_index')
            abstract = reconstruct(index)
            if index and not abstract:
                return {'status': 'parser_failed'}
        elif provider == 'europepmc':
            records = json.loads(body)['resultList']['result']
            if not records:
                return {'status': 'record_not_found'}
            matches = [r for r in records if doi(r.get('doi')) == identifier]
            if not matches:
                return {'status': 'content_mismatch'}
            abstracts = {plain(r.get('abstractText')) for r in matches if r.get('abstractText')}
            if len(abstracts) > 1:
                return {'status': 'content_mismatch'}
            abstract = next(iter(abstracts), '')
        else:
            raise ValueError('Unknown abstract provider')
        return {'status': 'available', 'abstract': abstract} if abstract else {'status': 'abstract_missing'}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {'status': 'parser_failed'}


def fetch_material(http, provider, url, identifier):
    meta, body = http.get(url, 'text/html' if provider == 'nature' else 'application/json')
    status = meta.get('status')
    result = {'provider': provider, 'url': url, 'http_status': status,
              'retry_after': meta.get('retry_after')}
    if status == 429:
        result['status'] = 'rate_limited'
    elif status in (401, 403):
        result['status'] = 'access_blocked'
    elif status == 404:
        result['status'] = 'record_not_found'
    elif not meta.get('ok'):
        result['status'] = 'request_failed'
    elif any(signal in body[:100000].lower() for signal in (b'verify you are human', b'just a moment...', b'are you a robot?', b'<title>client challenge</title>')):
        result['status'] = 'access_blocked'
    else:
        result.update(parse_material(provider, body, identifier))
    return result


def retry_at(status, attempts, clock, requests):
    # Failed parsing/identity and denied access need inspection, not blind retries.
    if attempts >= 3 or status in {'access_blocked', 'parser_failed', 'content_mismatch'}:
        return None
    target = clock + timedelta(days=1 if attempts == 1 else 3)
    for request in requests:
        value = request.get('retry_after')
        if not value:
            continue
        try:
            requested = clock + timedelta(seconds=max(0, int(value)))
        except ValueError:
            try:
                requested = parsedate_to_datetime(value)
                if requested.tzinfo is None:
                    requested = requested.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, OverflowError):
                continue
        target = max(target, requested)
    return target.isoformat()


def resume(db, ids=(), providers=()):
    """Explicit recovery after checking access/material; never performs a request."""
    if not ids and not providers:
        raise ValueError('Specify paper IDs or providers to resume')
    if any(p not in PROVIDERS for p in providers):
        raise ValueError('Unknown abstract provider')
    with worker_lock(DATA), db:
        for pid in ids:
            row = db.execute('SELECT state FROM papers WHERE id=?', (pid,)).fetchone()
            if not row or row['state'] != 'awaiting_abstract':
                raise ValueError('Only papers awaiting abstracts can be resumed')
            db.execute('DELETE FROM enrichment_schedule WHERE paper_id=?', (pid,))
            db.execute("UPDATE papers SET enrichment_state='not_attempted',revision=revision+1 WHERE id=?", (pid,))
        for provider in providers:
            db.execute('DELETE FROM enrichment_holds WHERE provider=?', (provider,))
        detail = {'paper_ids': list(ids), 'providers': list(providers)}
        db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                   (now(), 'enrichment-resume', 'manual', json.dumps(detail)))
    return detail


def input_digest(paper):
    # Abstract arrival and title/type correction invalidate the previous retry decision.
    material = [paper.get(k) for k in ('doi', 'title', 'type', 'abstract')]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()


def run(db, max_items, *, http=None, clock=None, cohort='monitoring', ids=None):
    if max_items < 1:
        raise ValueError('max-items must be positive')
    clock = clock or datetime.now(timezone.utc)
    cfg = json.loads((ROOT / 'config/runtime.json').read_text('utf-8'))
    http = http or HTTP(DATA / 'enrichment' / now().replace(':', '').replace('+', '_'),
                        max_attempts=1, interval_seconds=max(2, cfg['request_interval_seconds']))
    added = attempted = stale = 0
    active = registry()
    with worker_lock(DATA):
        held_providers = {r['provider']: dict(r) for r in db.execute('SELECT * FROM enrichment_holds')
                          if not r['retry_at'] or datetime.fromisoformat(r['retry_at']) > clock}
        rows = db.execute("SELECT * FROM papers WHERE state='awaiting_abstract' AND cohort=? ORDER BY id",(cohort,)).fetchall()
        if ids is not None:rows=[r for r in rows if r['id'] in ids]
        for row in rows:
            if attempted >= max_items:
                break
            paper = json.loads(row['data'])
            decision = json.loads(row['result'] or '{}')
            identifier = doi(paper.get('doi'))
            if row['journal'] not in active or not identifier or not decision.get('title_screened'):
                continue
            digest = input_digest(paper)
            schedule = db.execute('SELECT * FROM enrichment_schedule WHERE paper_id=?', (row['id'],)).fetchone()
            attempts = 0
            if schedule and schedule['input_digest'] == digest:
                if not schedule['next_attempt_at'] or datetime.fromisoformat(schedule['next_attempt_at']) > clock:
                    continue
                attempts = schedule['attempts']
            routes = []
            if identifier.startswith('10.1038/'):
                routes.append(('nature', 'https://www.nature.com/articles/' + identifier.split('/', 1)[1],
                               'Nature publisher Abstract section'))
            routes.extend([
                ('openalex', 'https://api.openalex.org/works/https://doi.org/' + quote(identifier, safe='/'),
                 'OpenAlex, exact DOI verified'),
                ('europepmc', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urlencode({
                    'query': 'DOI:"' + identifier + '"', 'format': 'json', 'resultType': 'core'}),
                 'Europe PMC, exact DOI verified')])
            requests = []
            found = None
            for provider, url, source in routes:
                if provider in held_providers:
                    hold = held_providers[provider]
                    requests.append({'provider': provider, 'status': 'provider_deferred',
                                     'hold_status': hold['status'], 'held_until': hold['retry_at']})
                    continue
                result = fetch_material(http, provider, url, identifier)
                requests.append({k: v for k, v in result.items() if k != 'abstract'})
                if result['status'] in {'access_blocked', 'rate_limited'}:
                    # Survives process restarts and applies even if another source succeeds.
                    until = retry_at(result['status'], 1, clock, [result])
                    held_providers[provider] = {'status': result['status'], 'retry_at': until}
                    with db:
                        db.execute('INSERT OR REPLACE INTO enrichment_holds VALUES(?,?,?)',
                                   (provider, result['status'], until))
                if result['status'] == 'available':
                    found = {**result, 'source': source}
                    break
            did_request = any(r['status'] != 'provider_deferred' for r in requests)
            attempted += int(did_request)
            statuses = {r['status'] for r in requests}
            status = 'available' if found else next((s for s in (
                'content_mismatch', 'parser_failed', 'access_blocked', 'rate_limited', 'request_failed',
                'provider_deferred', 'record_not_found', 'abstract_missing') if s in statuses), 'request_failed')
            next_at = None if found else retry_at(status, attempts + 1, clock, requests)
            # A blocked route must not suppress later indexing on another public source.
            retryable = {'rate_limited', 'request_failed', 'record_not_found', 'abstract_missing'}
            if not found and not statuses.intersection({'content_mismatch', 'parser_failed'}):
                if statuses.intersection(retryable):
                    next_at = retry_at('request_failed', attempts + 1, clock, requests)
                elif 'provider_deferred' in statuses and attempts + 1 < 3:
                    due = [r['held_until'] for r in requests if r.get('held_until')]
                    next_at = min(due) if due else None
            # Skipped providers never consume a paper's attempt budget or discard Retry-After.
            count = attempts + int(did_request)
            if not did_request:
                due = [r['held_until'] for r in requests if r.get('held_until')]
                next_at = min(due) if due else None
            with db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT revision,state FROM papers WHERE id=?', (row['id'],)).fetchone()
                if not current or current['revision'] != row['revision'] or current['state'] != 'awaiting_abstract':
                    stale += 1
                    continue  # Preserve a concurrent manual correction; retry result never wins.
                if found:
                    ingest(db, {**paper, 'abstract': found['abstract'], 'abstract_source': found['source'],
                                'abstract_url': found['url']}, found['url'])
                    db.execute('UPDATE papers SET result=? WHERE id=?', (row['result'], row['id']))
                    added += 1
                else:
                    db.execute('UPDATE papers SET revision=revision+1,updated_at=? WHERE id=?', (now(), row['id']))
                db.execute('UPDATE papers SET enrichment_state=? WHERE id=?', (status, row['id']))
                db.execute('INSERT OR REPLACE INTO enrichment_schedule VALUES(?,?,?,?,?,?)',
                           (row['id'], digest, count, next_at, status, now()))
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                           (now(), 'enrich', status, json.dumps({'paper_id': row['id'], 'attempt': count,
                            'next_attempt_at': next_at, 'needs_manual_check': not found and next_at is None,
                            'requests': requests}, ensure_ascii=False)))
            print('abstract', row['id'], status, 'next', next_at or 'none', flush=True)
    return {'attempted': attempted, 'abstracts_added': added, 'stale_results_ignored': stale}
