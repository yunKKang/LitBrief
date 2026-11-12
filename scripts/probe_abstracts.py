"""Small deterministic OpenAlex enrichment probe; does not estimate whole-journal coverage."""
import json
import time
import sys
from html.parser import HTMLParser
from pathlib import Path
from audit_sources import HTTP, OUTPUT, combined_works, doi, load, save, selected_journals

TARGETS = ['Nature Communications', 'Nature Sustainability', 'Nature Water',
           'Nature Reviews Earth & Environment', 'Nature Reviews Clean Technology',
           'One Earth', 'Joule', 'Environmental Science & Ecotechnology',
           'Resources, Conservation and Recycling', 'Building and Environment',
           'Cities', 'Water Research', 'Journal of Industrial Ecology']


def reconstruct(index):
    if not isinstance(index, dict):
        return None
    words = {}
    for word, positions in index.items():
        if not isinstance(positions, list):
            return None
        for p in positions:
            if not isinstance(p, int) or p < 0 or p in words:
                return None
            words[p] = word
    if not words or set(words) != set(range(max(words) + 1)):
        return None
    return ' '.join(words[p] for p in range(len(words)))


class AbstractSection(HTMLParser):
    """Extract only the publisher's named abstract section, never meta description."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.text = []
        self.citation_doi = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and a.get('name') == 'citation_doi':
            self.citation_doi = doi(a.get('content'))
        if tag == 'div':
            if self.div_depth:
                self.div_depth += 1
            elif a.get('id') == 'Abs1-content':
                self.div_depth = 1
        if self.div_depth and tag in ('p', 'br'):
            self.text.append(' ')

    def handle_endtag(self, tag):
        if tag == 'div' and self.div_depth:
            self.div_depth -= 1

    def handle_data(self, data):
        if self.div_depth:
            self.text.append(data)


def probe_publisher():
    http = HTTP(OUTPUT)
    results = []
    for issn in ['2398-9629', '2731-6084', '2662-138X', '3005-0685', '2731-9997']:
        journal = load(OUTPUT / 'journals' / (issn + '.json'))
        works = combined_works(journal)
        catalogue = load(OUTPUT / 'catalogue' / (issn + '.json'))
        choices = [e for e in catalogue['items'] if e['article_type'] in ('Article', 'Analysis', 'Review Article', 'Review', 'Perspective') and e['doi'] in works and not works[e['doi']]['abstract']]
        if not choices:
            results.append({'journal': journal['title'], 'status': 'no_eligible_sample'})
            continue
        entry = choices[0]
        meta, body = http.get(entry['url'], 'text/html')
        p = AbstractSection()
        if meta['ok']:
            p.feed(body.decode('utf-8', errors='replace'))
        abstract = ' '.join(''.join(p.text).split()) if p.citation_doi == entry['doi'] else None
        results.append({'journal': journal['title'], 'sample': entry, 'request': meta,
                        'doi_verified': p.citation_doi == entry['doi'], 'abstract': abstract or None,
                        'source_selector': '#Abs1-content', 'sample_rule': 'newest catalogue research/review/perspective entry missing Crossref abstract, one per named journal'})
        save(OUTPUT / 'publisher-abstract-probe.json', results)
        print(journal['title'], 'HTTP', meta.get('status'), 'abstract=', bool(abstract), flush=True)
    save(OUTPUT / 'publisher-abstract-probe.json', results)


def main():
    http = HTTP(OUTPUT)
    candidates = {}
    active_titles = {j['title'] for j in selected_journals()}
    for p in (OUTPUT / 'journals').glob('*.json'):
        j = load(p)
        if j['title'] not in TARGETS or j['title'] not in active_titles:
            continue
        eligible = [w for w in combined_works(j).values() if not w['abstract'] and not any(t in w['title'].lower() for t in ['correction', 'editorial', 'obituary'])]
        eligible.sort(key=lambda w: w.get('created') or '')
        if eligible:
            candidates[j['title']] = eligible[0]
    results = []
    for title in TARGETS:
        if title not in candidates:
            continue
        work = candidates[title]
        meta, body = http.get('https://api.openalex.org/works/https://doi.org/' + work['doi'], 'application/json')
        result = {'journal': title, 'doi': work['doi'], 'title': work['title'], 'sample_rule': 'oldest-created eligible missing-abstract record in audit candidate set, one per selected journal',
                  'request': meta, 'abstract': None, 'doi_verified': False}
        if meta['ok']:
            try:
                data = json.loads(body)
                result['doi_verified'] = doi(data.get('doi')) == work['doi']
                result['openalex_id'] = data.get('id')
                if result['doi_verified']:
                    result['abstract'] = reconstruct(data.get('abstract_inverted_index'))
            except (ValueError, TypeError) as exc:
                result['parse_error'] = str(exc)
        else:
            result['response_excerpt'] = body.decode(errors='replace')[:500]
        results.append(result)
        save(OUTPUT / 'abstract-probe.json', results)
        print(title, 'HTTP', meta.get('status'), 'abstract=', bool(result['abstract']), flush=True)
        time.sleep(1)


if __name__ == '__main__':
    probe_publisher() if '--publisher' in sys.argv else main()
