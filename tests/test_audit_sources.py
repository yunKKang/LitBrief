import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_sources import doi, crossref_date, fetch_window, parse_feed, parse_nature_listing, publication_scope, combined_works, reconcile, selected_journals
from probe_abstracts import AbstractSection, reconstruct


class FakeHTTP:
    def __init__(self, messages):
        self.messages = iter(messages)

    def json(self, url):
        msg = next(self.messages)
        return {'cache_key': 'test', 'error': 'temporary source failure'}, msg


def item(identifier):
    return {'DOI': identifier, 'title': ['A paper'], 'resource': {'primary': {'URL': 'https://example.org/pii/S123456'}}}


class AuditTests(unittest.TestCase):
    def test_month_only_publication_overlaps_midmonth_window(self):
        work = {'dates': {'published': '2026-10'}}
        self.assertEqual(publication_scope(work, '2026-10-13', '2026-11-11'), 'uncertain_overlap')
        exact = {'dates': {'published': '2026-10-05'}}
        self.assertEqual(publication_scope(exact, '2026-10-13', '2026-11-11'), 'outside_window')

    def test_month_query_padding_does_not_admit_exact_old_dates(self):
        old = {'doi': '10.1000/old', 'dates': {'published': '2026-10-05'}}
        uncertain = {'doi': '10.1000/uncertain', 'dates': {'published': '2026-10'}}
        j = {'window': {'start': '2026-10-13', 'end': '2026-11-11'}, 'windows': [{'kind': 'pub', 'works': [old, uncertain]}]}
        self.assertEqual(set(combined_works(j)), {'10.1000/uncertain'})

    def test_named_abstract_section_excludes_teaser(self):
        p = AbstractSection()
        p.feed('<meta name="citation_doi" content="10.1000/a"><meta name="description" content="teaser"><div id="Abs1-content"><p>Study of CH<sub>4</sub>.</p></div><div>Unrelated</div>')
        self.assertEqual(p.citation_doi, '10.1000/a')
        self.assertEqual(''.join(p.text).strip(), 'Study of CH4.')

    def test_openalex_reconstruction_rejects_missing_positions(self):
        self.assertEqual(reconstruct({'A': [0], 'study': [1]}), 'A study')
        self.assertIsNone(reconstruct({'A': [0], 'study': [2]}))

    def test_nature_listing_decodes_pagination_and_scopes_to_cards(self):
        html = b'<a href="/articles/sidebar">Sidebar</a><article><a href="/articles/s123">Paper</a><time datetime="2026-10-20"></time><span class="c-meta__type">Article</span></article><li data-page="next"><a href="/ncomms/articles?page&#x3D;2&amp;year&#x3D;2026">Next</a></li>'
        result = parse_nature_listing(html)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['next_url'], 'https://www.nature.com/ncomms/articles?page=2&year=2026')

    def test_rsc_doi_in_description_and_update_date_are_distinguished(self):
        xml = b'<rss xmlns:a="http://www.w3.org/2005/Atom"><channel><title>RSC</title><item><title>Paper</title><description>DOI: 10.1039/D6EE00123A, Paper</description><a:updated>2026-06-30</a:updated></item></channel></rss>'
        e = parse_feed(xml)['items'][0]
        self.assertEqual(e['doi'], '10.1039/d6ee00123a')
        self.assertEqual(e['date_role'], 'updated_only')
        self.assertEqual(reconcile({}, [e], '2026-06-01', '2026-07-01')[0]['feed_date_scope'], 'unknown')

    def test_selected_scope_includes_observation_journals_and_excludes_removed(self):
        journals = selected_journals()
        self.assertEqual(len(journals), 27)
        self.assertIn('Nature Reviews Clean Technology', {j['title'] for j in journals})
        self.assertIn('Communications Earth & Environment', {j['title'] for j in journals})
        self.assertNotIn('Cities', {j['title'] for j in journals})
        for retired in ('Joule', 'The Innovation', 'Energy & Environmental Science'):
            self.assertNotIn(retired, {j['title'] for j in journals})
        self.assertNotIn('Journal of Industrial Ecology', {j['title'] for j in journals})
        self.assertIn('Applied Energy', {j['title'] for j in journals})
        self.assertIn('Resources, Conservation and Recycling', {j['title'] for j in journals})
        self.assertIn('Building and Environment', {j['title'] for j in journals})
        self.assertNotIn('Journal of Cleaner Production', {j['title'] for j in journals})
        self.assertNotIn('Water Research', {j['title'] for j in journals})
        self.assertIn('Nature Food', {j['title'] for j in journals})
        self.assertIn('Nature Health', {j['title'] for j in journals})
        self.assertIn('The Lancet Planetary Health', {j['title'] for j in journals})
        self.assertNotIn('Environmental Research Letters', {j['title'] for j in journals})
        self.assertNotIn('Energy and Buildings', {j['title'] for j in journals})
        self.assertNotIn('Water Resources Research', {j['title'] for j in journals})

    def test_registry_matches_selected_journals(self):
        import json
        root = Path(__file__).resolve().parents[1]
        registry = json.loads((root / 'config' / 'sources.json').read_text(encoding='utf-8'))
        self.assertEqual({(j['title'], j['seed_issn']) for j in registry}, {(j['title'], j['seed_issn']) for j in selected_journals()})

    def test_doi_normalization_keeps_valid_parentheses(self):
        self.assertEqual(doi('https://doi.org/10.1000/ABC(1)'), '10.1000/abc(1)')
        self.assertIsNone(doi('https://example.org/not-a-doi'))

    def test_partial_dates_are_not_fabricated(self):
        self.assertEqual(crossref_date({'date-parts': [[2026, 11]]}), '2026-11')
        self.assertIsNone(crossref_date(None))

    def test_rdf_feed_doi_and_date(self):
        xml = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns="http://purl.org/rss/1.0/"><channel><title>Journal</title></channel><item><title>Study</title><link>https://www.nature.com/articles/s12345</link><dc:identifier>doi:10.1000/ABC</dc:identifier><dc:date>2026-11-01</dc:date><description>Only a teaser</description></item></rdf:RDF>'''
        entry = parse_feed(xml)['items'][0]
        self.assertEqual(entry['doi'], '10.1000/abc')
        self.assertEqual(entry['date'], '2026-11-01')
        self.assertFalse(entry['feed_description_is_verified_abstract'])

    def test_html_block_page_is_not_an_empty_feed(self):
        with self.assertRaises(ValueError):
            parse_feed(b'<html><title>Access denied</title></html>')

    def test_pagination_collects_every_page(self):
        http = FakeHTTP([{'items': [item('10.1000/a'), item('10.1000/b')], 'total-results': 3, 'next-cursor': 'two'},
                         {'items': [item('10.1000/c')], 'total-results': 3}])
        result = fetch_window(http, '0000-0000', '2026-10-13', '2026-11-11', 'pub', page_size=2)
        self.assertTrue(result['complete'])
        self.assertEqual(result['unique_dois'], 3)

    def test_failed_page_does_not_certify_range(self):
        http = FakeHTTP([{'items': [item('10.1000/a')], 'total-results': 2, 'next-cursor': 'two'}, None])
        result = fetch_window(http, '0000-0000', '2026-10-13', '2026-11-11', 'pub', page_size=1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['unique_dois'], 1)

    def test_duplicate_rows_cannot_satisfy_total(self):
        http = FakeHTTP([{'items': [item('10.1000/a'), item('10.1000/a')], 'total-results': 2}])
        result = fetch_window(http, '0000-0000', '2026-10-13', '2026-11-11', 'pub', page_size=3)
        self.assertFalse(result['complete'])

    def test_title_match_is_not_promoted_to_doi_proof(self):
        work = {'title': 'A useful study', 'piis': []}
        entry = {'title': 'A useful study', 'doi': None, 'pii': None, 'date': None}
        result = reconcile({'10.1000/a': work}, [entry], '2026-10-13', '2026-11-11')
        self.assertEqual(result[0]['match_method'], 'exact_normalized_title_candidate')
        self.assertEqual(result[0]['feed_date_scope'], 'unknown')


if __name__ == '__main__':
    unittest.main()
