from contextlib import closing
from datetime import datetime,timedelta,timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import retention
from library import connect,ingest,review
from test_workflow import PAPER
from webapp import export_site


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'outputs';self.root.mkdir()
        self.db=connect(self.root/'library/library.sqlite3')
        self.clock=datetime.now(timezone.utc)
        self.old=(self.clock-timedelta(days=120)).isoformat()

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def add(self,suffix='fixture',state='ai_excluded',old=True):
        with self.db:
            pid=ingest(self.db,{**PAPER,'doi':'10.1000/'+suffix,'urls':['https://example.org/'+suffix]},'fixture')
            self.db.execute('UPDATE papers SET state=?,result=?,updated_at=? WHERE id=?',
                            (state,json.dumps({'decision':'exclude','reason':'Irrelevant','evidence':[PAPER['abstract']]}),self.old if old else self.clock.isoformat(),pid))
        return pid

    def request(self,ids,status='applied'):
        path=self.root/'ai-trial/request.response.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'abstract':PAPER['abstract']}),encoding='utf-8')
        rows=[dict(self.db.execute('SELECT * FROM papers WHERE id=?',(pid,)).fetchone()) for pid in ids]
        with self.db:self.db.execute('INSERT INTO ai_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('request','a'*64,'abstracts',json.dumps(rows),json.dumps({'input':PAPER['abstract'],'model':'fixture'}),str(path),status,None,'{}',self.old,self.old))
        return path

    def cache(self,folder='source-audit-fixture/raw',age=40):
        path=self.root/folder/'response.body';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'Synthetic HTTP body')
        meta=path.with_suffix('.json');meta.write_text(json.dumps({'ok':True,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}),encoding='utf-8')
        at=(self.clock-timedelta(days=age)).timestamp()
        for p in (path,meta):os.utime(p,(at,at))
        return path,meta

    def plan(self):return retention.build_plan(self.db,self.root,self.clock)

    def apply(self,plan):return retention.apply_plan(self.db,plan,plan['id'],self.root)

    def test_preview_is_read_only_and_compaction_preserves_identity_and_decision(self):
        pid=self.add();raw=self.request([pid]);body,meta=self.cache()
        before=retention.db_digest(self.db);plan=self.plan()
        self.assertEqual(before,retention.db_digest(self.db));self.assertTrue(raw.exists())
        self.assertEqual([r['id'] for r in plan['papers']],[pid])
        result=self.apply(plan)
        self.assertEqual(result['papers_compacted'],1);self.assertTrue(Path(result['backup']).is_file())
        self.assertFalse(raw.exists());self.assertFalse(body.exists());self.assertFalse(meta.exists())
        row=self.db.execute('SELECT * FROM papers').fetchone()
        self.assertEqual(row['state'],'ai_excluded');self.assertTrue(json.loads(row['data'])['retention_compacted'])
        self.assertEqual(json.loads(row['result'])['reason'],'Irrelevant')
        self.assertTrue(self.db.execute('SELECT 1 FROM identities WHERE paper_id=?',(pid,)).fetchone())
        for table,columns in [('papers',['data','result']),('observations',['data']),('ai_requests',['inputs','request_json'])]:
            for r in self.db.execute('SELECT '+','.join(columns)+' FROM '+table):
                self.assertNotIn('PRIVATE_ABSTRACT',str(tuple(r)))

    def test_rediscovery_does_not_restore_payload_or_repeat_ai(self):
        pid=self.add('example');self.apply(self.plan())
        with self.db:self.assertEqual(ingest(self.db,PAPER,'rediscovery'),pid)
        row=self.db.execute('SELECT * FROM papers WHERE id=?',(pid,)).fetchone()
        self.assertEqual(row['state'],'ai_excluded');self.assertFalse(json.loads(row['data'])['abstract'])
        self.assertNotIn('PRIVATE_ABSTRACT',str([tuple(r) for r in self.db.execute('SELECT data FROM observations')]))
        review(self.db,pid,'requeue','',[],'Explicit reconsideration',row['revision'])
        with self.db:ingest(self.db,PAPER,'new evidence')
        row=self.db.execute('SELECT * FROM papers WHERE id=?',(pid,)).fetchone()
        self.assertEqual(row['state'],'pending');self.assertEqual(json.loads(row['data'])['abstract'],PAPER['abstract'])

    def test_human_feedback_and_unresolved_or_mixed_requests_are_protected(self):
        first=self.add('first');second=self.add('second',state='awaiting_review')
        raw=self.request([first,second]);self.assertEqual(self.plan()['papers'],[])
        self.assertTrue(raw.exists())
        with self.db:self.db.execute('DELETE FROM ai_requests')
        row=self.db.execute('SELECT revision FROM papers WHERE id=?',(first,)).fetchone()
        review(self.db,first,'reject','',[],'Calibration feedback',row[0])
        with self.db:self.db.execute('UPDATE papers SET updated_at=? WHERE id=?',(self.old,first))
        self.assertEqual(self.plan()['papers'],[])

    def test_unresolved_single_request_protects_its_material(self):
        pid=self.add();raw=self.request([pid],'billing_unknown')
        self.assertEqual(self.plan()['papers'],[])
        self.assertNotIn(raw.relative_to(self.root).as_posix(),[f['path'] for f in self.plan()['files']])

    def test_shared_legacy_file_prevents_incomplete_purge_claim(self):
        pid=self.add();legacy=self.root/'legacy.json';legacy.write_text(PAPER['abstract'])
        with self.db:self.db.execute('UPDATE observations SET origin=? WHERE paper_id=?',(str(legacy),pid))
        self.assertEqual(self.plan()['papers'],[])

    def test_unregistered_model_cache_requires_attribution_before_compaction(self):
        self.add();folder=self.root/'ai-trial';folder.mkdir()
        (folder/'old-unregistered.json').write_text(json.dumps({'evidence':PAPER['abstract']}),encoding='utf-8')
        self.assertEqual(self.plan()['papers'],[])

    def test_changed_file_or_database_rejects_stale_confirmation(self):
        pid=self.add();body,_=self.cache();plan=self.plan();body.write_bytes(b'new contents')
        with self.assertRaises(ValueError):self.apply(plan)
        self.assertFalse(json.loads(self.db.execute('SELECT data FROM papers').fetchone()[0]).get('retention_compacted'))
        plan=self.plan()
        with self.db:self.db.execute('UPDATE papers SET revision=revision+1 WHERE id=?',(pid,))
        with self.assertRaises(ValueError):self.apply(plan)

    def test_tampered_path_and_missing_confirmation_never_delete(self):
        self.cache();plan=self.plan()
        with self.assertRaises(ValueError):retention.apply_plan(self.db,plan,'wrong',self.root)
        forged=copy.deepcopy(plan);forged['files'][0]['path']='../outside.txt'
        forged['id']=retention.digest({k:v for k,v in forged.items() if k!='id'})
        with self.assertRaises(ValueError):self.apply(forged)
        with self.assertRaises(ValueError):retention.checked_path(self.root,'../outside.txt')

    def test_database_identity_is_part_of_confirmation(self):
        self.add();plan=self.plan()
        with closing(sqlite3.connect(self.root/'clone.sqlite3')) as clone:self.db.backup(clone)
        with closing(connect(self.root/'clone.sqlite3')) as clone:
            with self.assertRaises(ValueError):retention.apply_plan(clone,plan,plan['id'],self.root)

    def test_backup_rotation_keeps_recent_and_daily_weekly_representatives(self):
        cfg={**retention.config(),'backup_daily':2,'backup_weekly':1,'backup_min_days':1}
        folder=self.root/'library/backups';folder.mkdir()
        for age in (0,1,2,3):
            p=folder/f'{age}.sqlite3'
            with closing(sqlite3.connect(p)) as db:db.execute('CREATE TABLE fixture(value)')
            at=(self.clock-timedelta(days=age)).timestamp();os.utime(p,(at,at))
        with patch.object(retention,'config',return_value=cfg):
            plan=self.plan();self.assertEqual({p['path'] for p in plan['files']},{'library/backups/2.sqlite3','library/backups/3.sqlite3'})
            self.apply(plan)
        self.assertTrue((folder/'0.sqlite3').exists());self.assertTrue((folder/'1.sqlite3').exists())

    def test_current_release_and_predecessor_are_preserved(self):
        cfg={**retention.config(),'release_previous':1}
        releases=[export_site(self.db,self.root/'public') for _ in range(4)]
        latest=(self.root/'public/latest.json').read_bytes()
        with patch.object(retention,'config',return_value=cfg):
            plan=self.plan();self.assertTrue(plan['files']);self.apply(plan)
        self.assertEqual((self.root/'public/latest.json').read_bytes(),latest)
        for p in releases[-2:]:self.assertTrue((p/'index.html').is_file())
        for p in releases[:2]:self.assertFalse((p/'index.html').exists())

    def test_partial_file_failure_can_be_replanned_after_database_compaction(self):
        pid=self.add();raw=self.request([pid]);plan=self.plan();original=Path.unlink
        def fail(path,*args,**kwargs):
            if path.resolve()==raw.resolve():raise PermissionError('Synthetic busy file')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail):result=self.apply(plan)
        self.assertTrue(result['failed_files']);self.assertTrue(raw.exists())
        next_plan=self.plan();self.assertFalse(next_plan['papers']);self.assertTrue(next_plan['files'])
        self.apply(next_plan);self.assertFalse(raw.exists())

    def test_material_queue_preserves_enrichment_cache(self):
        self.add(state='awaiting_abstract');body,_=self.cache('library/enrichment/fixture/raw')
        plan=self.plan();self.assertFalse(plan['files']);self.assertTrue(body.exists())

    def test_new_unknown_tables_require_review_before_cleanup(self):
        with self.db:self.db.execute('CREATE TABLE future_material_copies(value TEXT)')
        with self.assertRaises(ValueError):self.plan()

    def test_expired_or_future_dated_plans_are_rejected(self):
        self.add()
        for offset in (-2,2):
            plan=retention.build_plan(self.db,self.root,self.clock+timedelta(days=offset))
            with self.assertRaises(ValueError):self.apply(plan)

    def test_reparse_point_is_not_followed(self):
        from types import SimpleNamespace
        p=self.root/'ordinary';p.write_text('fixture')
        with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=0,st_file_attributes=1024)):
            with self.assertRaises(ValueError):retention.checked_path(self.root,'ordinary')


if __name__=='__main__':unittest.main()
