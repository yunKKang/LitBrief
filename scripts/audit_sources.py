"""Read-only, resumable source audit. No LLM, publication, or credentials required."""
from __future__ import annotations

import argparse
import calendar
from collections import Counter
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs' / 'source-audit-20261111'
USER_AGENT = 'LiteratureHOT/0.1 (read-only journal metadata audit)'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def plain(text):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def doi(value):
    value = unquote(unescape(str(value or ''))).strip()
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value, flags=re.I)
    value = re.sub(r'^doi:\s*', '', value, flags=re.I)
    if re.fullmatch(r'10\.\d{4,9}/[^\s<>"?#]+', value, re.I):
        return value.lower()
    return None


def doi_in(value):
    value = unquote(unescape(str(value or '')))
    m = re.search(r'10\.\d{4,9}/[^\s<>"?#]+', value, re.I)
    return doi(m.group(0)) if m else None


def pii_in(value):
    m = re.search(r'/pii/([A-Z0-9]+)', str(value or ''), re.I)
    return m.group(1).upper() if m else None


def normalize_title(value):
    return re.sub(r'[^a-z0-9]', '', plain(value).lower())


def crossref_date(value):
    parts = (value or {}).get('date-parts', [[]])[0]
    # Do not invent a day for a year-only or month-only date.
    return '-'.join(str(p).zfill(4 if i == 0 else 2) for i, p in enumerate(parts)) or None


def parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.date().isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return None


def issn_valid(value):
    s = value.replace('-', '')
    if not re.fullmatch(r'\d{7}[\dX]', s):
        return False
    return (sum(int(x) * (8-i) for i, x in enumerate(s[:7])) + (10 if s[-1] == 'X' else int(s[-1]))) % 11 == 0


def selected_journals():
    result = [{k: source[k] for k in ('title', 'seed_issn', 'display')}
              for source in load(ROOT / 'config/sources.json')]
    if any(not issn_valid(j['seed_issn']) for j in result) or len({j['seed_issn'] for j in result}) != len(result):
        raise ValueError('Invalid or duplicate journal ISSN')
    return result


class HTTP:
    def __init__(self, output, retry_failed=False, max_attempts=3, interval_seconds=1):
        if max_attempts < 1 or interval_seconds < 0:
            raise ValueError('HTTP attempt and interval limits must be valid')
        self.output = output
        self.retry_failed = retry_failed
        self.last = {}
        self.max_attempts = max_attempts
        self.interval_seconds = interval_seconds

    def get(self, url, accept='*/*'):
        key = hashlib.sha256(url.encode()).hexdigest()
        meta_file = self.output / 'raw' / (key + '.json')
        body_file = self.output / 'raw' / (key + '.body')
        previous_attempts = []
        if meta_file.exists():
            meta = load(meta_file)
            if meta['ok'] or not self.retry_failed:
                return meta, body_file.read_bytes() if body_file.exists() else b''
            previous_attempts = meta.get('attempts', [])
        host = urlsplit(url).netloc
        attempts = list(previous_attempts)
        for attempt in range(self.max_attempts):
            delay = max(self.interval_seconds, 1.5 if host == 'api.crossref.org' else 1.0) - (time.monotonic() - self.last.get(host, 0))
            if delay > 0:
                time.sleep(delay)
            self.last[host] = time.monotonic()
            body, headers = b'', {}
            meta = {'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'cache_key': key}
            try:
                agent = USER_AGENT if host == 'api.crossref.org' else 'Mozilla/5.0 (compatible; LiteratureHOT/0.1; metadata-audit)'
                req = Request(url, headers={'User-Agent': agent, 'Accept': accept})
                with urlopen(req, timeout=25) as response:
                    headers = dict(response.headers)
                    body = response.read(32 * 1024 * 1024 + 1)
                    if len(body) > 32 * 1024 * 1024:
                        raise ValueError('Response exceeded 32 MiB audit limit')
                    meta.update(ok=True, status=response.status, final_url=response.url, content_type=response.headers.get('Content-Type', ''))
            except HTTPError as exc:
                headers = dict(exc.headers)
                with exc:
                    body = exc.read(1024 * 1024)
                meta.update(ok=False, status=exc.code, error=str(exc), final_url=exc.url)
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                meta.update(ok=False, status=None, error=f'{type(exc).__name__}: {exc}')
            meta['retry_after'] = next((v for k, v in headers.items() if k.lower() == 'retry-after'), None)
            attempts.append({k: v for k, v in meta.items() if k != 'cache_key'})
            if meta['ok'] or meta['status'] not in (429, 500, 502, 503, 504) or attempt == self.max_attempts - 1:
                break
            try:
                wait = float(meta['retry_after'] or 5 * (attempt + 1))
            except ValueError:
                wait = 10
            if wait > 30:
                meta['error'] += '; Retry-After > 30s, deferred for later run'
                break
            time.sleep(max(wait, 2))
        meta.update(attempts=attempts, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_bytes(body)
        save(meta_file, meta)
        return meta, body

    def json(self, url):
        meta, body = self.get(url, 'application/json')
        if not meta['ok']:
            return meta, None
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get('message'), dict):
                raise ValueError('Not a Crossref message object')
            return meta, data['message']
        except (ValueError, TypeError) as exc:
            return {**meta, 'ok': False, 'error': f'JSON contract: {exc}'}, None


def work_record(item):
    primary = (item.get('resource') or {}).get('primary', {}).get('URL')
    urls = [primary, item.get('URL')] + [x.get('URL') for x in item.get('link', [])]
    return {
        'doi': doi(item.get('DOI')), 'title': plain((item.get('title') or [''])[0]),
        'issns': item.get('ISSN', []), 'container_title': item.get('container-title', []),
        'publisher': item.get('publisher'), 'type': item.get('type'),
        'dates': {k: crossref_date(item.get(k)) for k in ('published', 'published-online', 'published-print')},
        'created': item.get('created', {}).get('date-time'), 'indexed': item.get('indexed', {}).get('date-time'),
        'deposited': item.get('deposited', {}).get('date-time'),
        'abstract': plain(item.get('abstract')), 'urls': [u for u in urls if u],
        'piis': sorted({pii_in(u) for u in urls if pii_in(u)}),
    }


FIELDS = 'DOI,title,ISSN,container-title,publisher,type,published,published-online,published-print,created,indexed,deposited,abstract,URL,link,resource'


def fetch_window(http, issn, start, end, kind, page_size=500):
    cursor, seen_cursor, works, missing_doi, pages, references = '*', set(), {}, 0, 0, []
    total, complete, error = None, False, None
    # Crossref treats a month-only date as the first day of that month in filters.
    query_start = start[:8] + '01' if kind == 'pub' else start
    while pages < 100:
        params = {'filter': f'from-{kind}-date:{query_start},until-{kind}-date:{end}',
                  'rows': page_size, 'cursor': cursor, 'select': FIELDS}
        meta, msg = http.json(f'https://api.crossref.org/journals/{issn}/works?' + urlencode(params))
        references.append(meta['cache_key'])
        if msg is None:
            error = meta.get('error', 'Failed Crossref request')
            break
        items = msg.get('items')
        if not isinstance(items, list):
            error = 'items is not an array'
            break
        pages += 1
        total = msg.get('total-results')
        before = len(works)
        for item in items:
            record = work_record(item)
            if record['doi']:
                works[record['doi']] = record
            else:
                missing_doi += 1
        if len(items) < page_size:
            complete = (isinstance(total, int) and len(works) == total and missing_doi == 0)
            if not complete:
                error = f'Unique DOI count {len(works)} != reported total {total}, missing DOI rows {missing_doi}'
            break
        next_cursor = msg.get('next-cursor')
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursor or len(works) == before:
            error = 'Missing/repeated cursor or no new DOI; range not certified complete'
            break
        seen_cursor.add(cursor)
        cursor = next_cursor
    if pages >= 100 and not complete:
        error = 'Audit page limit reached'
    return {'kind': kind, 'complete': complete, 'reported_total': total, 'unique_dois': len(works),
            'query_start': query_start, 'query_end': end,
            'pages': pages, 'error': error, 'raw_requests': references, 'works': list(works.values())}


def run_crossref(args):
    http = HTTP(OUTPUT, args.retry_failed)
    registry = {j['seed_issn']: j for j in load(ROOT / 'config' / 'sources.json')}
    for j in selected_journals():
        if args.only and j['seed_issn'] not in args.only:
            continue
        path = OUTPUT / 'journals' / (j['seed_issn'] + '.json')
        existing = load(path) if path.exists() else {}
        if existing.get('crossref_complete') and existing.get('query_policy') == 'month-overlap-v1' and existing.get('window') == {'start': args.start, 'end': args.end} and not args.retry_failed:
            print('cached', j['title'], flush=True)
            continue
        query_issn = registry[j['seed_issn']].get('crossref_query_issn', j['seed_issn'])
        meta, identity = http.json('https://api.crossref.org/journals/' + query_issn)
        result = {**j, 'window': {'start': args.start, 'end': args.end}, 'identity_request': meta['cache_key'],
                  'identity': identity, 'identity_error': meta.get('error'), 'crossref_query_issn': query_issn,
                  'query_policy': 'month-overlap-v1', 'windows': []}
        if identity:
            for kind in ('pub', 'created'):
                result['windows'].append(fetch_window(http, query_issn, args.start, args.end, kind))
            result['crossref_complete'] = all(w['complete'] for w in result['windows'])
        else:
            result['crossref_complete'] = False
        save(path, result)
        print(j['title'], 'complete=', result['crossref_complete'],
              [(w['kind'], w['unique_dois'], w['error']) for w in result['windows']], flush=True)


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def parse_feed(body):
    root = ET.fromstring(body)
    if local_name(root.tag).lower() not in ('rss', 'rdf', 'feed'):
        raise ValueError('Response is not RSS/RDF/Atom')
    title_nodes = [e for e in root.iter() if local_name(e.tag) == 'title']
    feed_title = plain(''.join(title_nodes[0].itertext())) if title_nodes else ''
    entries = []
    for item in root.iter():
        if local_name(item.tag) not in ('item', 'entry'):
            continue
        fields = {}
        links = []
        for child in item:
            name = local_name(child.tag)
            value = ''.join(child.itertext()).strip()
            fields.setdefault(name, []).append(value)
            if name == 'link':
                links.append(child.attrib.get('href') or value)
        text = lambda names: next((v for name in names for v in fields.get(name, []) if v), '')
        identifier = text(['doi', 'identifier', 'guid', 'id'])
        item_doi = doi_in(identifier) or next((doi_in(u) for u in links if doi_in(u)), None)
        description = plain(text(['description', 'summary', 'encoded', 'content']))
        if not item_doi:
            explicit = re.search(r'\bDOI\s*:\s*(10\.\d{4,9}/[^,\s<>]+)', description, re.I)
            item_doi = doi(explicit.group(1)) if explicit else None
        date_field = next((name for name in ['publicationDate', 'published', 'date', 'pubDate', 'coverDate', 'updated'] if text([name])), None)
        d = text([date_field]) if date_field else ''
        normalized_date = parse_date(d)
        date_role = 'updated_only' if date_field == 'updated' else ('publisher_date' if date_field else 'unknown')
        if not d:
            description_date = re.search(r'Publication date:\s*(Available online\s+)?(.+?)(?:\s+Source:|$)', description, re.I)
            if description_date:
                d = description_date.group(2).strip()
                date_role = 'available_online' if description_date.group(1) else 'issue_or_cover_date'
                for fmt in ('%d %B %Y', '%B %d, %Y'):
                    try:
                        normalized_date = datetime.strptime(d, fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
        entries.append({'doi': item_doi, 'title': plain(text(['title'])), 'urls': links,
                        'pii': next((pii_in(u) for u in links if pii_in(u)), None),
                        'date': normalized_date, 'raw_date': d or None, 'date_role': date_role,
                        'description': description, 'feed_description_is_verified_abstract': False,
                        'categories': fields.get('category', [])})
    return {'feed_title': feed_title, 'items': entries}


def run_publisher(args):
    registry = load(ROOT / 'config' / 'sources.json')
    http = HTTP(OUTPUT, args.retry_failed)
    for j in registry:
        if args.only and j['seed_issn'] not in args.only:
            continue
        results = []
        for source in j['publisher_sources']:
            meta, body = http.get(source['url'], 'application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.1')
            result = {**source, 'request': meta, 'parsed': None}
            if meta['ok']:
                try:
                    result['parsed'] = parse_feed(body)
                    expected = source.get('expected_feed_title')
                    if expected and result['parsed']['feed_title'] != expected:
                        result['parsed'] = None
                        result['parse_error'] = 'Feed journal identity mismatch'
                except (ET.ParseError, ValueError) as exc:
                    result['parse_error'] = str(exc)
            results.append(result)
        save(OUTPUT / 'publisher' / (j['seed_issn'] + '.json'), {**j, 'sources': results})
        print(j['title'], [(x.get('parsed') or {}).get('feed_title', x['request'].get('error', x.get('parse_error'))) for x in results], flush=True)


def publication_scope(work, start, end):
    value = work['dates'].get('published-online') or work['dates'].get('published') or work['dates'].get('published-print')
    if not value:
        return 'unknown'
    if len(value) == 10:
        return 'in_window' if start <= value <= end else 'outside_window'
    parts = [int(p) for p in value.split('-')]
    lo = date(parts[0], parts[1] if len(parts) > 1 else 1, 1)
    month = parts[1] if len(parts) > 1 else 12
    hi = date(parts[0], month, calendar.monthrange(parts[0], month)[1])
    return 'uncertain_overlap' if lo.isoformat() <= end and hi.isoformat() >= start else 'outside_window'


def combined_works(j):
    result = {}
    for window in j.get('windows', []):
        for work in window['works']:
            scope = publication_scope(work, j['window']['start'], j['window']['end'])
            if window['kind'] == 'pub' and scope == 'outside_window':
                continue
            record = result.setdefault(work['doi'], {**work, 'publication_window_scope': scope, 'discovered_via_windows': []})
            record['discovered_via_windows'].append(window['kind'])
    return result


def reconcile(works, feed_items, start, end):
    piimap, titlemap = {}, {}
    for key, w in works.items():
        for p in w.get('piis', []):
            piimap.setdefault(p, []).append(key)
        titlemap.setdefault(normalize_title(w['title']), []).append(key)
    result = []
    for item in feed_items:
        matched, method = None, None
        if item['doi'] in works:
            matched, method = item['doi'], 'doi'
        elif item.get('pii') and len(piimap.get(item['pii'], [])) == 1:
            matched, method = piimap[item['pii']][0], 'pii'
        elif len(titlemap.get(normalize_title(item['title']), [])) == 1:
            matched, method = titlemap[normalize_title(item['title'])][0], 'exact_normalized_title_candidate'
        d = item.get('date')
        date_status = 'unknown' if not d or item.get('date_role') == 'updated_only' else ('in_window' if start <= d <= end else 'outside_window')
        result.append({**item, 'matched_doi': matched, 'match_method': method, 'feed_date_scope': date_status})
    return result


def run_report(args):
    summary, candidates, pending, catalogue_checks = [], {}, [], []
    for j in selected_journals():
        path = OUTPUT / 'journals' / (j['seed_issn'] + '.json')
        cr = load(path) if path.exists() else {}
        if cr and (cr.get('window') != {'start': args.start, 'end': args.end} or cr.get('query_policy') != 'month-overlap-v1'):
            raise ValueError(f"Stale or mixed Crossref audit window for {j['title']}; recollect before reporting")
        works = combined_works(cr)
        for key, work in works.items():
            candidates[key] = {**work, 'journal_seed_issn': j['seed_issn'], 'journal_title': j['title'], 'display': j['display'],
                               'abstract_source': 'Crossref' if work['abstract'] else None}
        pp = OUTPUT / 'publisher' / (j['seed_issn'] + '.json')
        sources = load(pp)['sources'] if pp.exists() else []
        items = [item for source in sources if source.get('parsed') for item in source['parsed']['items']]
        matches = reconcile(works, items, args.start, args.end)
        save(OUTPUT / 'reconciliation' / (j['seed_issn'] + '.json'), matches)
        # Feed entries can repeat across official feeds. Count distinct identities only.
        unique = {}
        for item in matches:
            unique[item.get('doi') or item.get('pii') or normalize_title(item['title'])] = item
        matches = list(unique.values())
        exact = [m for m in matches if m['match_method'] in ('doi', 'pii')]
        tentative = [m for m in matches if m['match_method'] == 'exact_normalized_title_candidate']
        unresolved = [m for m in matches if not m['matched_doi']]
        pending.extend({'journal': j['title'], 'journal_seed_issn': j['seed_issn'], **m} for m in unresolved)
        catalogue_path = OUTPUT / 'catalogue' / (j['seed_issn'] + '.json')
        cat = load(catalogue_path) if catalogue_path.exists() else None
        if cat:
            missing = [e for e in cat['items'] if e['doi'] not in works]
            catalogue_checks.append({'journal': j['title'], 'journal_seed_issn': j['seed_issn'],
                                     'range_traversed': cat['range_traversed'], 'items': len(cat['items']),
                                     'matched': len(cat['items']) - len(missing), 'missing': missing,
                                     'pages': cat['pages'], 'error': cat['error'],
                                     'overlapping_items_across_pages': cat.get('overlapping_items_across_pages', 0)})
        identity = cr.get('identity') or {}
        expected = normalize_title(j['title']).replace('and', '')
        actual = normalize_title(identity.get('title', '')).replace('and', '')
        known_aliases = {
            'theinnovation': 'innovation',
            'globalenvironmentalchange': 'globalenvironmentalchangehumanpolicydimensions',
            'proceedingsofthenationalacademyofsciencesoftheunitedstatesofamerica': 'proceedingsofthenationalacademyofsciences',
            'thelancetplanetaryhealth': 'thelancetplanetaryhealth',
        }
        identity_matches = expected == actual or known_aliases.get(expected) == actual
        family = set(identity.get('ISSN', []))
        conflicting = [w['doi'] for w in works.values() if not family.intersection(w['issns'])]
        row = {**j, 'crossref_complete': cr.get('crossref_complete', False),
               'identity_title': identity.get('title'), 'identity_title_matches': identity_matches,
               'issn_family': sorted(family), 'crossref_query_issn': cr.get('crossref_query_issn', j['seed_issn']),
               'work_issn_conflicts': conflicting, 'publisher': identity.get('publisher'),
               'crossref_union_count': len(works), 'with_abstract': sum(bool(w['abstract']) for w in works.values()),
               'window_counts': {w['kind']: w['unique_dois'] for w in cr.get('windows', [])},
               'publisher_feed_ok': sum(bool(s.get('parsed')) for s in sources),
               'publisher_entries_unique': len(matches), 'doi_or_pii_matched': len(exact),
               'title_match_candidates': len(tentative), 'unresolved_feed_entries': len(unresolved),
               'dated_unresolved_in_window': sum(m['feed_date_scope'] == 'in_window' for m in unresolved),
               'errors': [w['error'] for w in cr.get('windows', []) if w.get('error')],
               'publisher_catalogue_range_traversed': cat['range_traversed'] if cat else False,
               'catalogue_entries': len(cat['items']) if cat else None,
               'catalogue_missing_in_crossref': len(missing) if cat else None,
               'publisher_full_window_verified': False}
        summary.append(row)
    enrichment_added = []
    for filename, origin in [('abstract-probe.json', 'OpenAlex exact-DOI sample'), ('publisher-abstract-probe.json', 'Nature publisher Abstract section sample')]:
        path = OUTPUT / filename
        if path.exists():
            for probe in load(path):
                identifier = probe.get('doi') or probe.get('sample', {}).get('doi')
                if identifier in candidates and probe.get('doi_verified') and probe.get('abstract') and not candidates[identifier]['abstract']:
                    candidates[identifier]['abstract'] = probe['abstract']
                    candidates[identifier]['abstract_source'] = origin
                    enrichment_added.append(identifier)
    save(OUTPUT / 'summary.json', {'start': args.start, 'end': args.end, 'journals': summary,
                                  'enrichment_added': enrichment_added, 'candidate_scope_counts': dict(Counter(w['publication_window_scope'] for w in candidates.values()))})
    save(OUTPUT / 'candidates.json', list(candidates.values()))
    save(OUTPUT / 'publisher-pending.json', pending)
    save(OUTPUT / 'catalogue-reconciliation.json', catalogue_checks)
    oa_probe = load(OUTPUT / 'abstract-probe.json') if (OUTPUT / 'abstract-probe.json').exists() else []
    publisher_probe = load(OUTPUT / 'publisher-abstract-probe.json') if (OUTPUT / 'publisher-abstract-probe.json').exists() else []
    oa_success = sum(bool(p.get('abstract')) and p.get('doi_verified', False) for p in oa_probe)
    publisher_success = sum(bool(p.get('abstract')) and p.get('doi_verified', False) for p in publisher_probe)
    rows = ['# 期刊采集覆盖核验', '', f'观察窗口：{args.start} 至 {args.end}；仅元数据和摘要，无 AI 调用。', '',
            f"当前启用 {len(summary)} 本期刊，Crossref 两个查询窗口均完成 {sum(r['crossref_complete'] for r in summary)} 本；得到 {len(candidates):,} 个唯一 DOI 候选。它们是待核元数据记录，**不能解释为近 30 天新发表的相关论文数**。", '',
            '发表日期查询向前覆盖到起始月首日，再按日期精度筛选：准确日期在窗外的旧记录剔除，只有月份的重叠记录保留为日期待核；同时并入近期首次进入 Crossref 的记录，以覆盖未来卷期与仅有年份的情况。近期首次入库也不必然等于近期首次发表。', '',
            f"Crossref 本身提供 {sum(r['with_abstract'] for r in summary):,} 条非空摘要；小样本补全另增加 {len(enrichment_added)} 条。下表摘要数量仅统计 Crossref 原始返回。RSS 通常只保留最近若干条，不能代表全窗口目录；标题匹配仅为待核候选。", '',
            '| 期刊 | Crossref 窗口完整 | DOI 并集 | 有摘要 | 可读官方 RSS | RSS 唯一条目 | DOI/PII 对上 | 仅标题候选 | 待核 RSS 条目 |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        rows.append(f"| {r['title']} | {'是' if r['crossref_complete'] else '否/未完成'} | {r['crossref_union_count']} | {r['with_abstract']} | {r['publisher_feed_ok']} | {r['publisher_entries_unique']} | {r['doi_or_pii_matched']} | {r['title_match_candidates']} | {r['unresolved_feed_entries']} |")
    rows += ['', '## 出版社分页目录对账', '', '以下为 Nature 系列公开目录的逐页遍历结果。按发布日期倒序翻页，直到跨过窗口起点；保留翻页重复与网络失败记录。目录不是原子快照，不能据此宣称所有渠道永远零遗漏。其余出版社本轮主要核对最新 RSS，尚未完成全部历史目录对账。', '',
             '| 期刊 | 已跨过窗口起点 | 页数 | 窗内目录条目 | Crossref 对上 | 待核条目 |', '|---|---|---:|---:|---:|---:|']
    for c in catalogue_checks:
        rows.append(f"| {c['journal']} | {'是' if c['range_traversed'] else '否'} | {c['pages']} | {c['items']} | {c['matched']} | {len(c['missing'])} |")
    rows += ['', '## 已识别的问题及处理', '',
             '- **One Earth 的采集键已修正**：分区表种子 ISSN 2590-3330 在 Crossref 返回 404；出版社 RSS 与 Crossref 共同识别的查询键为 2590-3322。原种子保留作溯源，不把查不到当成没有文章。',
             '- **历史核验发现 JIE 出版方迁移**：当时核实为 Springer Nature。该刊随后被用户移除，其证据保留，当前采集已停用。',
             '- **Nature RSS 条数有限**：多本子刊 RSS 仅 8 条，本轮用分页文章目录交叉核对。生产采集需要目录和增量元数据并行。',
             '- **RSS 可读不等于更新可靠**：EES 的 HTTPS RSS 在本机 TLS 失败，官方 HTTP RSS 可读；但前两个条目的更新时间与 Crossref 入库时间均在 2026 年 6 月，不能依靠该 RSS 获取当前新文。',
             '- **期号日期、在线日期、首次入库日期分开**：仅有年/月的日期不补造到某一天；未来卷期不自动排除已上线文章。月份查询边界已修正。',
             '- **无摘要与无相关性分开**：Elsevier 期刊本次 Crossref 数据普遍不带摘要，RSS 描述又常只有作者和卷期；保留待补材料状态，不能按标题写结论。',
             '- **访问能力仍有边界**：ACS 直接请求与新浏览器页出现访问校验；ScienceDirect 经用户手动完成人机验证后，南京大学机构会话能读取一篇正式摘要、在线日期和期刊入口。该浏览器成功不能证明服务器可无人值守批量获取。',
             '- **源范围变更已生效**：Communications Earth & Environment、Nature Food、Nature Health、The Lancet Planetary Health 已纳入；RCR、Applied Energy、Building and Environment 按用户确认恢复为列表试运行来源。Cities、JCP、Journal of Hydrology、Water Research、JIE、Sustainable Cities and Society 等维持排除，其旧记录仅作历史证据，不进入当前候选集合。',
             '- **来源优先级**：新文发现以出版社最新目录、在线优先发表入口和文章页面为主，RSS 辅助；Crossref 用于元数据补充、去重和查漏。Crossref 查询完成不代表出版社全部新文已经覆盖。', '',
             '## 小样本补摘要与差异复核', '',
             f'- OpenAlex：保存的抽样证据共 {len(oa_probe)} 条，{oa_success} 条取得并核对 DOI 后补回摘要。抽样证据按当前启用名单注入候选，仍被排除的 Cities、JIE 等不注入；复跑抽样时仅查询启用来源。样本按缺失摘要和来源选取，不是随机样本，不可推广为总体补全率。',
             f'- Nature 出版社：保存的研究/综述/观点抽样共 {len(publisher_probe)} 条，{publisher_success} 条从正式 Abstract 栏取到摘要，并核对页面 citation_doi。没有拿 SEO description 或 RSS 导读冒充摘要。',
             '- Science Advances 最新 RSS 有 2 个窗内 DOI，逐个请求 Crossref 返回 404，已保留为出版社待核条目。它们不能静默丢失；也未被直接标成可发布。',
             '- Nature 目录有 1 条 Career Column 在 Crossref DOI 查询中返回 404；目录已识别其非研究类型。',
             '- Cities 历史样本页面的 citation_online_date 为 2026/09/19，citation_publication_date 为 2026/12/01；这证明其 RSS 的未来卷期不等于本观察窗口新上线。该刊已退出启用名单。', '',
             '## 文件与复核方法', '',
             '- `summary.json`：逐刊状态、身份与 ISSN 核验、日期精度统计。',
             '- `candidates.json`：当前启用来源的 DOI 候选；含材料出处、日期精度与发现窗口，尚未做 AI 相关性筛选。',
             '- `publisher-pending.json`：未与窗口候选对上的 RSS 条目，区分窗内、窗外、日期未知；未对上不等于漏采。后续处理必须同时接收这条待核队列，不能只消费 Crossref 候选而丢弃出版社独有条目。',
             '- `catalogue-reconciliation.json`：分页目录的匹配结果与未匹配条目。',
             '- `abstract-probe.json`、`publisher-abstract-probe.json`、`discrepancy-probes.json`：有来源请求定位的抽样证据。',
             '- `browser-article-evidence.json`、`browser-cities-home-evidence.json`：用户完成人机验证后的 DOM 核验，仅作本地访问证据。',
             '- `raw/`、`journals/`、`publisher/`、`catalogue/`、`reconciliation/`：缓存、每次请求时间、SHA-256、分页结果和错误。失败不是空结果；网络原始快照只供本地核验。', '',
             '本轮未调用付费模型，未复制浏览器认证 Cookie 到脚本或服务器，未下载论文 PDF，未发布网站。']
    (OUTPUT / 'coverage.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    print(json.dumps({'journals': len(summary), 'complete_crossref': sum(r['crossref_complete'] for r in summary),
                      'unique_dois': len(candidates), 'publisher_feed_accessible': sum(r['publisher_feed_ok'] > 0 for r in summary)}, ensure_ascii=False))


def parse_nature_listing(body):
    """Read article cards only, excluding navigation and related-link noise."""
    html = body.decode('utf-8', errors='replace')
    cards = re.findall(r'<article\b[^>]*>.*?</article>', html, re.S)
    items = []
    for card in cards:
        link = re.search(r'<a\b[^>]*href="(/articles/[^"?#]+)"[^>]*>(.*?)</a>', card, re.S)
        stamp = re.search(r'<time\b[^>]*datetime="([^"]+)"', card)
        article_type = re.search(r'<span\b[^>]*class="c-meta__type"[^>]*>(.*?)</span>', card, re.S)
        if not link or not stamp:
            raise ValueError('A listing article card has no recognized article URL or date')
        article_id = link.group(1).split('/')[-1]
        items.append({'doi': '10.1038/' + article_id, 'title': plain(link.group(2)),
                      'date': parse_date(stamp.group(1)), 'url': 'https://www.nature.com' + link.group(1),
                      'article_type': plain(article_type.group(1)) if article_type else None})
    next_block = re.search(r'<li\b[^>]*data-page="next"[^>]*>(.*?)</li>', html, re.S)
    next_link = re.search(r'<a\b[^>]*href="([^"]+)"', next_block.group(1)) if next_block else None
    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.S)
    return {'title': plain(title.group(1)) if title else '', 'items': items,
            'next_url': 'https://www.nature.com' + unescape(next_link.group(1)) if next_link else None}


def run_catalogue(args):
    from concurrent.futures import ThreadPoolExecutor
    if args.start[:4] != args.end[:4]:
        raise ValueError('This audit catalogue traversal is scoped to one calendar year')
    registry = [j for j in load(ROOT / 'config' / 'sources.json') if j['publisher_family'] == 'Nature Portfolio']
    if args.only:
        registry = [j for j in registry if j['seed_issn'] in args.only]

    def collect(j):
        http = HTTP(OUTPUT, args.retry_failed)
        url = j['homepage'] + 'articles?' + urlencode({'year': args.end[:4], 'page': 1})
        items, seen, refs, page_titles = {}, set(), [], []
        observed_dois, overlapping_items = set(), 0
        complete, error, previous_oldest = False, None, None
        for page in range(1, 151):
            if url in seen:
                error = 'Repeated catalogue URL'
                break
            seen.add(url)
            meta, body = http.get(url, 'text/html')
            refs.append(meta['cache_key'])
            if not meta['ok']:
                error = meta.get('error')
                break
            try:
                parsed = parse_nature_listing(body)
                page_titles.append(parsed['title'])
                entries = parsed['items']
                if not entries or any(not e['date'] for e in entries):
                    raise ValueError('Empty/unrecognized catalogue page or missing dates')
                dates = [e['date'] for e in entries]
                fresh = [e for e in entries if e['doi'] not in observed_dois]
                overlapping_items += len(entries) - len(fresh)
                if dates != sorted(dates, reverse=True) or (previous_oldest and fresh and max(e['date'] for e in fresh) > previous_oldest):
                    raise ValueError('Catalogue dates not monotonically newest-first; cannot stop safely')
                if not fresh:
                    raise ValueError('Catalogue page contains no new article identity')
                observed_dois.update(e['doi'] for e in entries)
                previous_oldest = min(dates)
                for e in entries:
                    if args.start <= e['date'] <= args.end:
                        items[e['doi']] = e
                print(f"catalogue {j['title']} page={page} oldest={min(dates)} collected={len(items)}", flush=True)
                if min(dates) < args.start or not parsed['next_url']:
                    complete = True
                    break
                url = parsed['next_url']
            except (ValueError, TypeError) as exc:
                error = str(exc)
                break
        else:
            error = 'Catalogue page limit reached'
        result = {**j, 'window': {'start': args.start, 'end': args.end}, 'range_traversed': complete,
                  'error': error, 'pages': len(refs), 'raw_requests': refs, 'page_titles': page_titles,
                  'overlapping_items_across_pages': overlapping_items,
                  'snapshot_is_atomic': False, 'items': list(items.values())}
        save(OUTPUT / 'catalogue' / (j['seed_issn'] + '.json'), result)
        print('catalogue finished', j['title'], complete, len(items), error, flush=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        list(pool.map(collect, registry))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['crossref', 'publisher', 'catalogue', 'report'])
    p.add_argument('--start', default='2026-10-13')
    p.add_argument('--end', default='2026-11-11')
    p.add_argument('--only', nargs='+')
    p.add_argument('--retry-failed', action='store_true')
    args = p.parse_args()
    assert date.fromisoformat(args.start) <= date.fromisoformat(args.end)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    {'crossref': run_crossref, 'publisher': run_publisher, 'catalogue': run_catalogue, 'report': run_report}[args.phase](args)


if __name__ == '__main__':
    main()
