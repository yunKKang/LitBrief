"""Paid-call boundaries exercised with synthetic responses and temporary SQLite only."""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch_screen as batch
from library import connect, ingest, review, archive_inactive_sources
from test_workflow import PAPER

CONFIG = {'base_url':'https://example.invalid/v1','api_key':'fixture-secret','model':'gpt-6-luna',
          'tier':'default','effort':'high','max_output_tokens':8000}


def answer(config, payload):
    rows = json.loads(payload['input'])
    items = [dict(id=r['id'],decision='exclude',reason='Synthetic exclusion',summary='',evidence=[],title_zh='',topics=[]) for r in rows]
    return {'status':'completed','usage':{'total_tokens':150,'input_tokens':100,'output_tokens':50},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':items})}]}]}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'library.sqlite3'
        self.db = connect(self.path)
        self.guard = patch.object(batch, 'DATA', self.root)
        self.guard.start()
        self.add()

    def tearDown(self):
        self.db.close(); self.guard.stop(); self.tmp.cleanup()

    def add(self, suffix='example'):
        with self.db:
            return ingest(self.db, {**PAPER, 'doi':'10.1000/'+suffix,'urls':['https://example.org/'+suffix]}, 'fixture')

    def run_batch(self, transport=answer, **kwargs):
        return batch.run(self.db, 100, group_size=1, titles_only=True, cache=self.root/'cache',
                         config=CONFIG, transport=transport, **kwargs)

    def request(self):
        return self.db.execute('SELECT * FROM ai_requests ORDER BY created_at DESC LIMIT 1').fetchone()

    def test_response_saved_before_interruption_recovers_without_network(self):
        transport = Mock(side_effect=answer)
        with patch.object(batch, 'apply_saved', side_effect=RuntimeError('crash before DB apply')):
            with self.assertRaises(RuntimeError): self.run_batch(transport)
        request = self.request()
        self.assertEqual(request['status'], 'response_saved')
        self.db.close(); self.db = connect(self.path)
        with patch.object(batch, 'post', side_effect=AssertionError('Recovery must be offline')):
            self.assertEqual(batch.recover(self.db, request['id'])['applied'], 1)
            self.assertTrue(batch.recover(self.db, request['id'])['already_resolved'])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0], 'ai_excluded')
        self.assertNotIn('fixture-secret', self.request()['request_json'])

    def test_timeout_requires_acknowledgment_and_explicit_requeue(self):
        send = Mock(side_effect=TimeoutError('simulated'))
        with self.assertRaises(TimeoutError): self.run_batch(send)
        request = self.request()
        self.assertEqual(request['status'], 'billing_unknown')
        self.assertEqual(self.run_batch(send)['processed'], 0)
        row = self.db.execute('SELECT * FROM papers').fetchone()
        with self.assertRaises(ValueError): review(self.db, row['id'], 'requeue', '', [], '', row['revision'])
        with self.assertRaises(ValueError): batch.recover(self.db, request['id'])
        batch.acknowledge(self.db, request['id'], 'Synthetic provider bill checked; allow reconsideration')
        review(self.db, row['id'], 'requeue', '', [], 'Explicit retry', row['revision'])
        self.assertEqual(self.run_batch()['processed'], 1)
        self.assertEqual(send.call_count, 1)

    def test_recovery_never_overrides_intervening_human_rejection(self):
        with patch.object(batch, 'apply_saved', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): self.run_batch()
        request = self.request()
        row = self.db.execute('SELECT * FROM papers').fetchone()
        review(self.db, row['id'], 'reject', '', [], 'Human decision', row['revision'])
        result = batch.recover(self.db, request['id'])
        self.assertEqual(result['applied'], 0)
        self.assertEqual(result['stale_results_ignored'], 1)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0], 'rejected')

    def test_live_response_never_overrides_concurrent_rejection(self):
        def transport(config, payload):
            with closing(connect(self.path)) as other:
                row = other.execute('SELECT * FROM papers').fetchone()
                review(other, row['id'], 'reject', '', [], 'Manual while waiting', row['revision'])
            return answer(config, payload)
        self.run_batch(transport)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0], 'rejected')

    def test_budget_boundary_keeps_unprocessed_candidates(self):
        second = self.add('second')
        result = self.run_batch()
        self.assertEqual(result['tokens'], 150)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(self.db.execute('SELECT state FROM papers WHERE id=?',(second,)).fetchone()[0], 'pending')

    def test_retired_source_is_retained_without_ai_or_approval(self):
        send = Mock(side_effect=AssertionError('Inactive source must not call AI'))
        with patch('library.registry',return_value={}), patch.object(batch,'registry',return_value={}):
            self.assertEqual(archive_inactive_sources(self.db),1)
            self.assertEqual(archive_inactive_sources(self.db),0)
            self.assertEqual(self.run_batch(send)['processed'],0)
            row=self.db.execute('SELECT * FROM papers').fetchone()
            self.assertEqual(row['cohort'],'inactive_source')
            with self.assertRaises(ValueError):review(self.db,row['id'],'approve','Summary',[],'',row['revision'])
        send.assert_not_called()

    def test_wrong_ids_keep_response_for_inspection_and_stop_requeue(self):
        def bad(config,payload):
            result=answer(config,payload)
            obj=json.loads(result['output'][0]['content'][0]['text']);obj['items'][0]['id']=999999
            result['output'][0]['content'][0]['text']=json.dumps(obj)
            return result
        with self.assertRaises(ValueError):self.run_batch(bad)
        self.assertEqual(self.request()['status'],'validation_failed')
        self.assertTrue(Path(self.request()['response_path']).is_file())
        row=self.db.execute('SELECT * FROM papers').fetchone()
        self.assertEqual(row['state'],'ai_error')
        with self.assertRaises(ValueError):review(self.db,row['id'],'requeue','',[],'',row['revision'])

    def test_known_raw_legacy_response_is_validated_without_billing(self):
        import hashlib
        rows=[dict(r) for r in self.db.execute('SELECT * FROM papers')]
        payload=batch.payload_for(rows,CONFIG,True)
        fingerprint=hashlib.sha256(json.dumps([CONFIG['base_url'],payload],sort_keys=True).encode()).hexdigest()
        cache=self.root/'cache';cache.mkdir()
        (cache/(fingerprint+'.response.json')).write_text(json.dumps(answer(CONFIG,payload)),encoding='utf-8')
        send=Mock(side_effect=AssertionError('Saved raw response should be reused'))
        self.assertEqual(self.run_batch(send)['tokens'],0)
        send.assert_not_called()

    def test_explicit_acknowledgment_can_retire_invalid_legacy_cache(self):
        import hashlib
        rows=[dict(r) for r in self.db.execute('SELECT * FROM papers')]
        payload=batch.payload_for(rows,CONFIG,True)
        fingerprint=hashlib.sha256(json.dumps([CONFIG['base_url'],payload],sort_keys=True).encode()).hexdigest()
        cache=self.root/'cache';cache.mkdir()
        (cache/(fingerprint+'.response.json')).write_text(json.dumps({'status':'incomplete'}),encoding='utf-8')
        with self.assertRaises(ValueError):self.run_batch(Mock(side_effect=AssertionError('Do not send before checking cache')))
        batch.acknowledge(self.db,self.request()['id'],'Incomplete cached response checked; permit a new attempt')
        row=self.db.execute('SELECT * FROM papers').fetchone()
        review(self.db,row['id'],'requeue','',[],'Retry explicitly',row['revision'])
        send=Mock(side_effect=answer)
        self.assertEqual(self.run_batch(send)['processed'],1)
        self.assertEqual(send.call_count,1)


if __name__ == '__main__': unittest.main()
