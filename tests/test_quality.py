from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import batch_screen
import quality
from library import connect, ingest, review, TOPICS, build_issue, publish_issue
from test_workflow import PAPER
from test_ai_recovery import CONFIG


def response(items):
    return {'status':'completed','usage':{'total_tokens':100,'input_tokens':70,'output_tokens':30},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':items})}]}]}


class QualityTests(unittest.TestCase):
    def test_one_invalid_quote_is_held_without_discarding_valid_peers(self):
        rows=[{'id':1,'data':json.dumps({'abstract':'Stock increased 10%.'})},
              {'id':2,'data':json.dumps({'abstract':'Trade reduced emissions.'})}]
        items=[{'id':1,'verdict':'supported','reason':'claim','evidence':['Stock increased 20%.'],'issues':[]},
               {'id':2,'verdict':'supported','reason':'claim','evidence':['Trade reduced emissions.'],'issues':[]}]
        result=quality.validate(response(items),rows,'verify',partial=True)
        self.assertEqual(result[1]['actor'],'system')
        self.assertEqual(result[1]['verdict'],'insufficient')
        self.assertEqual(result[2]['verdict'],'supported')
        with self.assertRaises(ValueError):quality.validate(response(items),rows,'verify')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=connect(self.root/'db.sqlite3')
        self.guard=patch.object(batch_screen,'DATA',self.root);self.guard.start()

    def tearDown(self):
        self.db.close();self.guard.stop();self.tmp.cleanup()

    def add(self,suffix='1',state='ai_excluded',title_pass=True,abstract=True):
        with self.db:
            pid=ingest(self.db,{**PAPER,'doi':'10.1000/'+suffix,'urls':['https://example.org/'+suffix],
                               'abstract':PAPER['abstract'] if abstract else ''},'fixture')
            result={'decision':'exclude','reason':'fixture','title_screened':title_pass}
            if state=='awaiting_review':
                result.update(decision='include',summary='Fixture summary',evidence=[PAPER['abstract']],title_zh='测试')
            self.db.execute('UPDATE papers SET state=?,result=? WHERE id=?',(state,json.dumps(result),pid))
        return pid

    def run_check(self,batch,send):
        return batch_screen.run(self.db,1000,group_size=3,max_groups=1,quality_batch=batch,
                                cache=self.root/'cache',config=CONFIG,transport=send)

    def test_sampling_is_repeatable_stratified_and_records_population(self):
        for i in range(12):self.add(str(i),state='type_excluded' if i%3==0 else 'ai_excluded',title_pass=i%3==1)
        first=quality.prepare(self.db,'audit','fixed',3)
        again=quality.prepare(self.db,'audit','fixed',3)
        self.assertEqual(first,again)
        report=quality.report(self.db,first['batch_id'])
        self.assertEqual({i['stage'] for i in report['items']},{'title','abstract','type'})
        self.assertEqual(sum(len(v) for v in report['metadata']['population'].values()),12)
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_jobs').fetchone()[0],3)

    def test_targeted_cases_are_separate_and_do_not_duplicate_random(self):
        for i in range(8):self.add(str(i))
        first=quality.prepare(self.db,'audit','seed',1)
        chosen=quality.report(self.db,first['batch_id'])['items'][0]['paper_id']
        other=next(i for i in range(1,9) if i!=chosen)
        second=quality.prepare(self.db,'audit','seed',1,[chosen,other])
        items=quality.report(self.db,second['batch_id'])['items']
        self.assertEqual(len(items),2)
        self.assertEqual({i['sample_kind'] for i in items},{'random','targeted'})

    def test_agent_disagreement_never_approves_or_changes_candidate(self):
        pid=self.add()
        before=dict(self.db.execute('SELECT * FROM papers').fetchone())
        batch=quality.prepare(self.db,'audit','fixture')['batch_id']
        send=Mock(return_value=response([{'id':pid,'verdict':'reconsider','reason':'系统问题值得复查',
                                         'evidence':[PAPER['abstract']],'issues':[]}]))
        self.run_check(batch,send)
        self.assertEqual(dict(self.db.execute('SELECT * FROM papers').fetchone()),before)
        outcome=quality.report(self.db,batch)['items'][0]
        self.assertEqual(outcome['result']['actor'],'agent')
        self.assertEqual(outcome['status'],'checked')
        self.assertEqual(self.run_check(batch,send)['processed'],0)
        self.assertEqual(send.call_count,1)

    def test_semantic_verification_prompt_covers_common_overclaims(self):
        pid=self.add(state='awaiting_review')
        batch=quality.prepare(self.db,'verify','fixture')['batch_id']
        mode,rows=quality.pending(self.db,batch)
        payload=quality.payload_for(rows,CONFIG,mode)
        for phrase in ('情景与实绩','相关与因果','强度与总量','地区与全球','不确定性'):
            self.assertIn(phrase,payload['instructions'])
        self.assertNotIn('api_key',payload)
        self.run_check(batch,lambda *args:response([{'id':pid,'verdict':'unsupported','reason':'将情景误写为实绩',
                                                    'evidence':[PAPER['abstract']],'issues':['scenario_vs_observed']}]))
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0],'awaiting_review')

    def test_no_abstract_or_wrong_quote_cannot_claim_confirmed_error(self):
        pid=self.add(abstract=False)
        rows=[dict(self.db.execute('SELECT * FROM papers').fetchone())]
        item={'id':pid,'verdict':'reconsider','reason':'Seems relevant','evidence':[],'issues':[]}
        with self.assertRaises(ValueError):quality.validate(response([item]),rows,'audit')
        item['verdict']='insufficient'
        self.assertEqual(quality.validate(response([item]),rows,'audit')[pid]['verdict'],'insufficient')
        item['evidence']=['invented quote']
        with self.assertRaises(ValueError):quality.validate(response([item]),rows,'audit')

    def test_missing_abstract_never_calls_model(self):
        self.add(abstract=False)
        batch=quality.prepare(self.db,'audit','fixture')['batch_id']
        send=Mock(side_effect=AssertionError('No abstract must not consume AI'))
        self.assertEqual(self.run_check(batch,send)['processed'],0)
        item=quality.report(self.db,batch)['items'][0]
        self.assertEqual(item['status'],'insufficient')
        self.assertEqual(item['result']['actor'],'system')
        send.assert_not_called()

    def test_feedback_protects_published_exclusion_without_rewriting_snapshot(self):
        pid=self.add(state='awaiting_review')
        review(self.db,pid,'approve','Summary',[TOPICS[4]],'fixture',1)
        issue=build_issue(self.db,date(2026,11,11));publish_issue(self.db,issue)
        before=self.db.execute('SELECT snapshot FROM issue_items').fetchone()[0]
        row=self.db.execute('SELECT * FROM papers').fetchone()
        quality.feedback(self.db,pid,'not_relevant','This is engineering, not systems',row['revision'])
        self.assertEqual(self.db.execute('SELECT snapshot FROM issue_items').fetchone()[0],before)
        self.assertEqual(self.db.execute('SELECT human_decision FROM papers').fetchone()[0],'reject')
        self.assertEqual(self.db.execute('SELECT actor FROM feedback').fetchone()[0],'human')

    def test_low_priority_does_not_become_domain_exclusion(self):
        pid=self.add(state='awaiting_review')
        quality.feedback(self.db,pid,'low_priority','Read later',1)
        row=self.db.execute('SELECT * FROM papers').fetchone()
        self.assertEqual(row['human_decision'],'')
        self.assertEqual(row['state'],'awaiting_review')

    def test_bad_summary_feedback_blocks_unpublished_draft(self):
        pid=self.add(state='awaiting_review')
        review(self.db,pid,'approve','Summary',[TOPICS[4]],'fixture',1)
        build_issue(self.db,date(2026,11,11))
        row=self.db.execute('SELECT * FROM papers').fetchone()
        quality.feedback(self.db,pid,'summary_issue','Causality overstated',row['revision'])
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0],'needs_review')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0],0)

    def test_quality_recovery_respects_intervening_user_feedback(self):
        pid=self.add()
        batch=quality.prepare(self.db,'audit','fixture')['batch_id']
        with patch.object(batch_screen,'apply_saved',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.run_check(batch,lambda *args:response([{'id':pid,'verdict':'reconsider','reason':'fixture','evidence':[PAPER['abstract']],'issues':[]}]))
        quality.feedback(self.db,pid,'not_relevant','Confirmed irrelevant',1)
        req=self.db.execute('SELECT id FROM ai_requests').fetchone()[0]
        self.assertEqual(batch_screen.recover(self.db,req)['stale_results_ignored'],1)
        self.assertEqual(quality.report(self.db,batch)['items'][0]['status'],'stale')
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0],'rejected')

    def test_feedback_http_returns_to_article_and_escapes_note(self):
        from http.server import HTTPServer
        from urllib.request import urlopen,Request
        from urllib.parse import urlencode
        from urllib.error import HTTPError
        import threading
        from webapp import make_handler
        pid=self.add(state='awaiting_review')
        server=HTTPServer(('127.0.0.1',0),make_handler(self.root/'db.sqlite3','fixture-csrf',self.root/'public'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        form={'csrf':'bad','id':pid,'revision':1,'category':'low_priority','note':'<script>synthetic</script>'}
        try:
            with self.assertRaises(HTTPError) as error:urlopen(Request(base+'/feedback',data=urlencode(form).encode()))
            self.assertEqual(error.exception.code,403);error.exception.close()
            form['csrf']='fixture-csrf'
            with urlopen(Request(base+'/feedback',data=urlencode(form).encode())) as res:
                self.assertEqual(res.url,base+f'/paper?id={pid}')
                text=res.read().decode('utf-8')
                self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;',text)
                self.assertNotIn('<script>synthetic</script>',text)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
