"""Resolve a bounded set of publisher-only observations, without fuzzy auto-merging."""
import json
from urllib.parse import urlencode, quote
from audit_sources import HTTP, OUTPUT, FIELDS, load, save, normalize_title, work_record, selected_journals


def main():
    http = HTTP(OUTPUT)
    results = []
    queries = [('Nature catalogue date mismatch', '10.1038/d41586-026-02668-1'),
               ('RSC feed freshness sample', '10.1039/d6ee03332f'),
               ('RSC feed freshness sample', '10.1039/d6ee02798a')]
    for label, identifier in queries:
        meta, msg = http.json('https://api.crossref.org/works/' + quote(identifier, safe='/'))
        results.append({'check': label, 'doi': identifier, 'request': meta,
                        'crossref': work_record(msg) if msg else None})
        save(OUTPUT / 'discrepancy-probes.json', results)
    active_issns = {j['seed_issn'] for j in selected_journals()}
    for issn in ['0264-2751', '2375-2548']:
        if issn not in active_issns:
            continue
        unmatched = [e for e in load(OUTPUT / 'reconciliation' / (issn + '.json')) if not e['matched_doi']][:2]
        for entry in unmatched:
            if entry['doi']:
                url = 'https://api.crossref.org/works/' + quote(entry['doi'], safe='/')
            else:
                url = 'https://api.crossref.org/works?' + urlencode({'query.bibliographic': entry['title'], 'filter': 'issn:' + issn, 'rows': 3, 'select': FIELDS})
            meta, msg = http.json(url)
            matches = []
            if msg:
                options = msg.get('items', [msg])
                for option in options:
                    record = work_record(option)
                    if entry['doi'] == record['doi'] or (normalize_title(entry['title']) == normalize_title(record['title']) and issn in record['issns']):
                        matches.append(record)
            results.append({'check': 'publisher_only_sample', 'issn': issn, 'publisher_entry': entry,
                            'request': meta, 'verified_matches': matches})
            save(OUTPUT / 'discrepancy-probes.json', results)
            print(issn, entry['title'][:90], 'exact matches', len(matches), flush=True)


if __name__ == '__main__':
    main()
