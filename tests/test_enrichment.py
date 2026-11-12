"""Offline queue tests: identity, bounded requests, restart recovery and manual edits."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
from email.utils import format_datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import enrichment
from library import connect, ingest, today

CLOCK = datetime(2026, 11, 12, tzinfo=timezone.utc)


def response(payload=None, status=200, retry=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return {'ok': status == 200, 'status': status, 'retry_after': retry}, body


class FakeHTTP:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def get(self, url, accept):
        self.calls.append(url)
        value = next(self.replies)
        return value() if callable(value) else value


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'test.sqlite3'
        self.db = connect(self.path)
        self.data_patch = patch.object(enrichment, 'DATA', self.root)
        self.data_patch.start()

    def tearDown(self):
        self.db.close()
        self.data_patch.stop()
        self.tmp.cleanup()

    def add(self, identifier='10.1000/example', screened=True, **extra):
        paper = {'journal_seed_issn': '2398-9629', 'doi': identifier,
                 'title': 'Housing and supply chain transition', 'type': 'Article',
                 'dates': {'published-online': today().isoformat()}, **extra}
        with self.db:
            pid = ingest(self.db, paper, 'fixture')
            self.db.execute('UPDATE papers SET result=? WHERE id=?',
                            (json.dumps({'title_screened': screened, 'reason': 'title relevant'}), pid))
        return pid

    def run_queue(self, replies, clock=CLOCK, limit=10):
        http = FakeHTTP(replies)
        result = enrichment.run(self.db, limit, http=http, clock=clock)
        return result, http

    def test_no_title_pass_or_inactive_source_never_requests(self):
        self.add(screened=False)
        pid = self.add('10.1000/retired')
        with self.db:
            self.db.execute("UPDATE papers SET journal='retired' WHERE id=?", (pid,))
        result, http = self.run_queue([])
        self.assertEqual(result['attempted'], 0)
        self.assertEqual(http.calls, [])

    def test_nature_uses_only_named_abstract_and_matching_doi(self):
        pid = self.add('10.1038/test')
        body = b'<meta name="citation_doi" content="10.1038/test"><meta name="description" content="TEASER"><div id="Abs1-content"><p>Formal abstract.</p></div>'
        result, http = self.run_queue([response(body)])
        row = self.db.execute('SELECT * FROM papers WHERE id=?', (pid,)).fetchone()
        self.assertEqual(result['abstracts_added'], 1)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(row['state'], 'pending')
        self.assertTrue(json.loads(row['result'])['title_screened'])
        self.assertEqual(json.loads(row['data'])['abstract'], 'Formal abstract.')

    def test_exact_europepmc_fallback_preserves_provenance(self):
        pid = self.add()
        result, http = self.run_queue([response(status=404), response({'resultList': {'result': [
            {'doi': '10.1000/example', 'abstractText': '<p>Formal evidence.</p>'}]}})])
        data = json.loads(self.db.execute('SELECT data FROM papers WHERE id=?', (pid,)).fetchone()[0])
        self.assertEqual(result['abstracts_added'], 1)
        self.assertIn('Europe PMC', data['abstract_source'])
        self.assertIn('resultType=core', http.calls[1])
        self.assertEqual(data['abstract_url'], http.calls[1])

    def test_delayed_index_retries_twice_then_pauses(self):
        pid = self.add()
        missing = [response(status=404), response({'resultList': {'result': []}})]
        self.run_queue(missing)
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(hours=1))[0]['attempted'], 0)
        self.run_queue(missing, clock=CLOCK + timedelta(days=1))
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(days=2))[0]['attempted'], 0)
        self.run_queue(missing, clock=CLOCK + timedelta(days=4))
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(days=10))[0]['attempted'], 0)
        schedule = self.db.execute('SELECT * FROM enrichment_schedule WHERE paper_id=?', (pid,)).fetchone()
        self.assertEqual(schedule['attempts'], 3)
        self.assertIsNone(schedule['next_attempt_at'])

    def test_identity_conflicts_never_enter_ai_queue(self):
        pid = self.add()
        self.run_queue([response({'doi': '10.1000/wrong', 'abstract_inverted_index': {'Wrong': [0]}}),
                        response({'resultList': {'result': []}})])
        row = self.db.execute('SELECT * FROM papers WHERE id=?', (pid,)).fetchone()
        self.assertEqual(row['state'], 'awaiting_abstract')
        self.assertEqual(row['enrichment_state'], 'content_mismatch')
        self.assertFalse(json.loads(row['data'])['abstract'])
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(days=2))[0]['attempted'], 0)

    def test_rate_limit_persists_across_restart_and_new_papers(self):
        first = self.add()
        future = CLOCK + timedelta(days=7)
        self.run_queue([response(status=429, retry=format_datetime(future)), response(status=403)])
        self.db.close()
        self.db = connect(self.path)
        second = self.add('10.1000/new')
        result, http = self.run_queue([], clock=CLOCK + timedelta(days=2))
        self.assertEqual(http.calls, [])
        self.assertEqual(result['attempted'], 0)
        row = self.db.execute('SELECT * FROM enrichment_schedule WHERE paper_id=?', (second,)).fetchone()
        self.assertEqual(row['attempts'], 0)
        self.assertEqual(row['next_attempt_at'], future.isoformat())
        # Resume only the paper: still respects the provider hold.
        enrichment.resume(self.db, [first])
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(days=3))[1].calls, [])

    def test_provider_hold_expires_without_network_retry_storm(self):
        self.add()
        self.run_queue([response(status=429, retry='172800'), response(status=429, retry='172800')])
        self.add('10.1000/new')
        self.assertEqual(self.run_queue([], clock=CLOCK + timedelta(days=1))[1].calls, [])
        result, http = self.run_queue([response(status=404), response({'resultList': {'result': []}})],
                                      clock=CLOCK + timedelta(days=2), limit=1)
        self.assertEqual(result['attempted'], 1)
        self.assertEqual(len(http.calls), 2)

    def test_explicit_recovery_is_offline_and_audited(self):
        pid = self.add()
        self.run_queue([response(status=403), response(status=403)])
        enrichment.resume(self.db, [pid], ['openalex'])
        self.assertIsNone(self.db.execute('SELECT * FROM enrichment_schedule').fetchone())
        self.assertEqual(self.db.execute('SELECT provider FROM enrichment_holds').fetchone()[0], 'europepmc')
        self.assertEqual(self.db.execute("SELECT count(*) FROM runs WHERE kind='enrichment-resume'").fetchone()[0], 1)
        result, _ = self.run_queue([response({'doi': '10.1000/example', 'abstract_inverted_index': {'Verified': [0]}})])
        self.assertEqual(result['abstracts_added'], 1)

    def test_denied_provider_does_not_stop_late_indexing_elsewhere(self):
        self.add()
        self.run_queue([response(status=403), response({'resultList': {'result': []}})])
        result, http = self.run_queue([response({'resultList': {'result': [
            {'doi': '10.1000/example', 'abstractText': 'Newly indexed evidence.'}]}})],
            clock=CLOCK + timedelta(days=1))
        self.assertEqual(result['abstracts_added'], 1)
        self.assertEqual(len(http.calls), 1)
        self.assertIn('europepmc', http.calls[0])

    def test_failed_recovery_rolls_back_all_selected_changes(self):
        pid = self.add()
        self.run_queue([response(status=403), response(status=403)])
        with self.assertRaises(ValueError):
            enrichment.resume(self.db, [pid, 99999], ['openalex'])
        self.assertIsNotNone(self.db.execute('SELECT * FROM enrichment_schedule').fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM enrichment_holds').fetchone()[0], 2)

    def test_concurrent_manual_edit_wins(self):
        pid = self.add()
        def manual_edit():
            with closing(connect(self.path)) as other, other:
                other.execute("UPDATE papers SET revision=revision+1,state='rejected' WHERE id=?", (pid,))
            return response({'doi': '10.1000/example', 'abstract_inverted_index': {'Fetched': [0]}})
        result, _ = self.run_queue([manual_edit])
        self.assertEqual(result['stale_results_ignored'], 1)
        self.assertEqual(self.db.execute('SELECT state FROM papers WHERE id=?', (pid,)).fetchone()[0], 'rejected')
        self.assertIsNone(self.db.execute('SELECT * FROM enrichment_schedule').fetchone())

    def test_metadata_correction_reopens_held_paper(self):
        pid = self.add()
        self.run_queue([response(b'invalid json'), response({'resultList': {'result': []}})])
        with self.db:
            row = self.db.execute('SELECT data FROM papers WHERE id=?', (pid,)).fetchone()
            data = json.loads(row[0]); data['title'] = 'Corrected housing title'
            self.db.execute('UPDATE papers SET data=?,revision=revision+1 WHERE id=?', (json.dumps(data), pid))
        result, _ = self.run_queue([response({'doi': '10.1000/example', 'abstract_inverted_index': {'Evidence': [0]}})])
        self.assertEqual(result['abstracts_added'], 1)

    def test_parser_failures_are_distinct_from_missing_fields(self):
        for provider, body, expected in [
            ('openalex', b'{}', 'content_mismatch'),
            ('openalex', b'{"doi":"10.1000/a"}', 'abstract_missing'),
            ('openalex', b'{"doi":"10.1000/a","abstract_inverted_index":{"a":[2]}}', 'parser_failed'),
            ('europepmc', b'{"resultList":{"result":[]}}', 'record_not_found'),
            ('europepmc', b'{"resultList":{"result":[{"doi":"10.1000/a"}]}}', 'abstract_missing'),
            ('europepmc', b'<html>Not JSON</html>', 'parser_failed'),
        ]:
            with self.subTest(provider=provider, body=body):
                self.assertEqual(enrichment.parse_material(provider, body, '10.1000/a')['status'], expected)


if __name__ == '__main__':
    unittest.main()
