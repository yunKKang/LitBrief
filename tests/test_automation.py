"""No-network acceptance of admission, closed-period publication and cycle budgets."""
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import automation
import batch_screen
import quality
from library import connect,ingest,review,worker_lock,TOPICS
from test_workflow import PAPER
from test_ai_recovery import CONFIG


def transport(config,payload):
    inputs=json.loads(payload['input']);items=[]
    for paper in inputs:
        if payload['text']['format']['name']=='quality_check':
            items.append({'id':paper['id'],'verdict':'supported','reason':'Synthetic support',
                          'evidence':[paper['abstract']],'issues':[]})
        elif 'abstract' not in paper:
            items.append({'id':paper['id'],'decision':'insufficient','reason':'Relevant title',
                          'summary':'','evidence':[],'title_zh':'','topics':[]})
        else:
            items.append({'id':paper['id'],'decision':'include','reason':'Housing system study',
                          'summary':'住房转型的系统研究。','evidence':[paper['abstract']],
                          'title_zh':'住房转型','topics':[TOPICS[4]]})
    return {'status':'completed','usage':{'total_tokens':100,'input_tokens':70,'output_tokens':30},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':items})}]}]}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=connect(self.root/'library.sqlite3')
        self.patches=[patch.object(automation,'DATA',self.root),patch.object(batch_screen,'DATA',self.root),
                      patch.object(automation,'AI_CACHE',self.root/'cache'),
                      patch.object(batch_screen,'settings',return_value=CONFIG),patch.object(batch_screen,'post',side_effect=transport)]
        for p in self.patches:p.start()
        self.day=date(2026,11,15)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.db.close();self.tmp.cleanup()

    def add(self,suffix='fixture'):
        with self.db:
            return ingest(self.db,{**PAPER,'doi':'10.1000/'+suffix,'urls':['https://example.org/'+suffix],
                                   'dates':{'published-online':'2026-11-12'}},'fixture')

    def ready(self,suffix='fixture'):
        pid=self.add(suffix)
        with self.db:self.db.execute('UPDATE papers SET result=? WHERE id=?',(json.dumps({'title_screened':True}),pid))
        batch_screen.run(self.db,1000,config=CONFIG,transport=transport,cache=self.root/'cache')
        qa=quality.prepare(self.db,'verify','fixture')['batch_id']
        batch_screen.run(self.db,1000,config=CONFIG,transport=transport,cache=self.root/'cache',quality_batch=qa)
        return pid

    def row(self,pid):return self.db.execute('SELECT * FROM papers WHERE id=?',(pid,)).fetchone()

    def test_verified_current_recommendation_is_admitted_as_agent_not_human(self):
        pid=self.ready()
        self.assertTrue(automation.admission(self.db,self.row(pid),self.day)['eligible'])
        self.assertEqual(automation.admit(self.db,self.day),[pid])
        row=self.row(pid)
        self.assertEqual(row['approval_actor'],'agent');self.assertEqual(row['human_decision'],'')
        self.assertFalse(json.loads(row['result'])['human_reviewed'])
        self.assertTrue(automation.admission(self.db,row,self.day)['eligible'])
        revision=row['revision'];automation.admit(self.db,self.day)
        self.assertEqual(self.row(pid)['revision'],revision)

    def test_policy_material_and_feedback_changes_each_prevent_admission(self):
        pid=self.ready()
        with patch.object(automation,'policy_hash',return_value='new-policy'):
            self.assertFalse(automation.admission(self.db,self.row(pid),self.day)['eligible'])
        quality.feedback(self.db,pid,'not_relevant','Human exclusion',self.row(pid)['revision'])
        self.assertEqual(automation.admission(self.db,self.row(pid),self.day)['reason'],'human_exclusion')
        self.assertEqual(automation.admit(self.db,self.day),[])

    def test_summary_edit_makes_verification_stale(self):
        pid=self.ready()
        with self.db:
            result=json.loads(self.row(pid)['result']);result['summary']='Changed conclusion'
            self.db.execute('UPDATE papers SET result=?,revision=revision+1 WHERE id=?',(json.dumps(result),pid))
        self.assertFalse(automation.admission(self.db,self.row(pid),self.day)['eligible'])

    def test_semantic_pass_without_screening_provenance_does_not_publish(self):
        pid=self.ready()
        with self.db:
            result=json.loads(self.row(pid)['result']);result.pop('request_id')
            self.db.execute('UPDATE papers SET result=? WHERE id=?',(json.dumps(result),pid))
        self.assertEqual(automation.admission(self.db,self.row(pid),self.day)['reason'],'screening_provenance_missing')

    def test_offline_shadow_never_calls_network_or_mutates_candidates(self):
        pid=self.ready();before=dict(self.row(pid))
        with patch('workflow.collect',side_effect=AssertionError('No collection')),patch.object(batch_screen,'run',side_effect=AssertionError('No AI')):
            result=automation.cycle(self.db,day=self.day)
        self.assertTrue(result['admission'][0]['eligible'])
        self.assertEqual(dict(self.row(pid)),before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM issues').fetchone()[0],0)

    def test_publication_requires_mode_gate_before_any_work(self):
        self.add()
        with patch('workflow.collect',side_effect=AssertionError('No side effects')):
            with self.assertRaises(ValueError):automation.cycle(self.db,live=True,publish=True,max_tokens=100,runtime={'publication_mode':'manual'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM issues').fetchone()[0],0)

    def test_closed_period_publishes_once_and_defers_late_arrivals(self):
        pid=self.ready()
        result=automation.cycle(self.db,publish=True,day=date(2026,11,14),output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(result['publication']['state'],'not_due')
        result=automation.cycle(self.db,publish=True,day=self.day,output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(result['publication']['state'],'exported')
        path=Path(result['publication']['public_directory'])
        self.assertIn('部分覆盖',(path/'index.html').read_text('utf-8'))
        again=automation.cycle(self.db,publish=True,day=self.day,output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(again['publication']['state'],'already_exported')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0],1)
        self.assertEqual(self.row(pid)['approval_actor'],'agent')
        late=self.ready('late')
        same=automation.cycle(self.db,publish=True,day=self.day,output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(same['publication']['state'],'already_exported')
        self.assertIsNone(self.db.execute('SELECT 1 FROM issue_items WHERE paper_id=?',(late,)).fetchone())
        later=automation.cycle(self.db,publish=True,day=date(2026,11,22),output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(later['publication']['issue_id'],'2026-11-15')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0],2)

    def test_export_failure_is_recoverable_without_reapproval(self):
        self.ready()
        with patch('webapp.export_site',side_effect=OSError('Disk interruption')):
            result=automation.cycle(self.db,publish=True,day=self.day,output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertIsNone(result['publication'])
        self.assertEqual(self.db.execute('SELECT state FROM issues').fetchone()[0],'published')
        next_run=automation.cycle(self.db,publish=True,day=date(2026,11,22),output=self.root/'public',runtime={'publication_mode':'automatic'})
        self.assertEqual(next_run['publication']['state'],'exported')

    def test_unsupported_current_verification_never_admits(self):
        pid=self.ready()
        with self.db:
            job=self.db.execute('SELECT rowid,result FROM quality_jobs').fetchone()
            value=json.loads(job['result']);value.update(verdict='unsupported',issues=['scope'])
            self.db.execute('UPDATE quality_jobs SET result=? WHERE rowid=?',(json.dumps(value),job['rowid']))
        self.assertEqual(automation.admission(self.db,self.row(pid),self.day)['reason'],'verification_not_supported')
        self.assertEqual(automation.admit(self.db,self.day),[])

    def test_material_update_revokes_approval_and_can_be_screened_again(self):
        pid=self.ready();automation.admit(self.db,self.day)
        paper=json.loads(self.row(pid)['data']);paper['abstract']='Changed publisher evidence.'
        with self.db:ingest(self.db,paper,'changed fixture')
        self.assertFalse(automation.admission(self.db,self.row(pid),self.day)['eligible'])
        self.assertEqual(self.row(pid)['state'],'pending')

    def test_metadata_only_update_refreshes_stale_agent_approval(self):
        pid=self.ready();automation.admit(self.db,self.day)
        with self.db:ingest(self.db,json.loads(self.row(pid)['data']),'repeat observation')
        self.assertEqual(automation.refresh_legacy(self.db)['stale_agent_approvals'],1)
        self.assertEqual(self.row(pid)['state'],'awaiting_review')
        self.assertEqual(self.row(pid)['approval_actor'],'')

    def test_explicit_priority_feedback_defers_only_that_period(self):
        pid=self.ready()
        with patch('quality.now',return_value='2026-11-12T08:00:00+00:00'):
            quality.feedback(self.db,pid,'low_priority','Read next issue',self.row(pid)['revision'])
        # A current human approval can coexist with a scheduling preference.
        row=self.row(pid);review(self.db,pid,'approve','Summary',[TOPICS[4]],'Approved substance',row['revision'])
        self.assertEqual(automation.admission(self.db,self.row(pid),self.day,'2026-11-14')['reason'],'deferred_by_feedback')
        self.assertTrue(automation.admission(self.db,self.row(pid),date(2026,11,22),'2026-11-21')['eligible'])

    def test_live_cycle_shares_one_budget_and_isolates_collection_failure(self):
        self.add()
        original=batch_screen.run
        def run(*args,**kwargs):return original(*args,**kwargs)
        with patch('workflow.collect',side_effect=OSError('One source blocked')),patch('workflow.enrich',return_value={'attempted':0}),patch.object(batch_screen,'run',side_effect=run):
            result=automation.cycle(self.db,live=True,max_tokens=250,max_groups=1,only=['2398-9629'],day=self.day)
        self.assertEqual(result['spent_tokens'],300) # Threshold checked between requests, one overrun.
        self.assertFalse(result['billing_unknown'])
        self.assertTrue(result['admission'][0]['eligible'])
        self.assertEqual(result['steps'][0]['status'],'failed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issues').fetchone()[0],0)

    def test_unknown_charge_stops_further_paid_stages_but_enrichment_continues(self):
        self.add()
        with patch('workflow.collect',return_value={}),patch('workflow.enrich',return_value={'attempted':0}) as enrich,patch.object(batch_screen,'post',side_effect=TimeoutError()):
            result=automation.cycle(self.db,live=True,max_tokens=1000,max_groups=1,only=['2398-9629'])
        self.assertTrue(result['billing_unknown']);enrich.assert_called_once()
        self.assertEqual(self.db.execute('SELECT count(*) FROM ai_requests').fetchone()[0],1)

    def test_nested_lock_allows_pipeline_but_excludes_other_threads(self):
        errors=[]
        def other():
            try:
                with worker_lock(self.root):pass
            except RuntimeError:errors.append('blocked')
        with worker_lock(self.root):
            with worker_lock(self.root):pass
            thread=threading.Thread(target=other);thread.start();thread.join()
        self.assertEqual(errors,['blocked'])

    def test_new_abstract_screen_clears_old_material_change_flag(self):
        pid=self.add()
        with self.db:self.db.execute("UPDATE papers SET material_changed=1,result=? WHERE id=?",(json.dumps({'title_screened':True}),pid))
        batch_screen.run(self.db,1000,config=CONFIG,transport=transport,cache=self.root/'cache')
        self.assertEqual(self.row(pid)['material_changed'],0)


if __name__=='__main__':unittest.main()
