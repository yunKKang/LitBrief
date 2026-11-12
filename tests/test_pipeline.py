import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from library import connect,ingest,establish_baseline,today
from prefilter import exclusion
from batch_screen import validate_group
from publisher_collect import ListingParser
from diagnose_sources import classify
from enrich_europepmc import extract

class PipelineTests(unittest.TestCase):
    def test_access_diagnosis_does_not_guess_403_reason(self):
        self.assertEqual(classify(403,'Forbidden'),'forbidden_unexplained')
        self.assertEqual(classify(403,'Just a moment; Enable JavaScript and cookies to continue'),'browser_challenge')
        self.assertEqual(classify(429,'captcha'),'rate_limited')
        self.assertEqual(classify(None,'','certificate_error'),'certificate_error')

    def test_abstract_supplement_checks_exact_doi(self):
        payload={'resultList':{'result':[{'doi':'10.1000/a','abstractText':'<p>Verified</p>'}]}}
        self.assertEqual(extract(payload,'10.1000/a'),'Verified')
        self.assertIsNone(extract(payload,'10.1000/b'))
    def test_types_preserve_research_and_uncertainty(self):
        for kind in ('Comment', 'Commentary'):
            self.assertIsNotNone(exclusion({'type':kind,'title':'Environmental implications of innovation'}))
        self.assertIsNone(exclusion({'type':'Discussion','title':'Food-system trade-offs'}))
        for kind in ['Article','Research article','Review','Perspective','journal-article','']:
            self.assertIsNone(exclusion({'type':kind,'title':'Industrial transition'}))
        for kind in ['News','Editorial','Author Correction','Book Review']:
            self.assertIsNotNone(exclusion({'type':kind,'title':'Industrial transition'}))
        self.assertIsNone(exclusion({'title':'Correction of biased estimates in supply-chain models'}))
        self.assertIsNotNone(exclusion({'title':'Author Correction: Industrial transition'}))

    def test_title_stage_cannot_invent_a_recommendation(self):
        row={'id':1,'data':json.dumps({'abstract':'Actual evidence'})}
        item={'id':1,'decision':'include','reason':'related','summary':'Claim','evidence':['Actual evidence'],'title_zh':'标题','topics':['产业与基础设施绿色转型']}
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':[item]})}]}]}
        with self.assertRaises(ValueError):validate_group(response,[row],True)
        item.update(decision='insufficient',summary='',evidence=[],title_zh='')
        response['output'][0]['content'][0]['text']=json.dumps({'items':[item]})
        self.assertEqual(validate_group(response,[row],True)[1]['decision'],'insufficient')

    def test_group_rejects_wrong_or_duplicate_identity(self):
        row={'id':1,'data':'{}'}
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"items":[{"id":2}]}'}]}]}
        with self.assertRaises(ValueError):validate_group(response,[row],True)

    def test_bad_evidence_does_not_lose_other_group_decisions(self):
        rows=[{'id':1,'data':'{"abstract":"Actual evidence"}'},{'id':2,'data':'{"abstract":"Other evidence"}'}]
        items=[{'id':1,'decision':'include','reason':'Related','summary':'Summary','evidence':['fabricated'],'title_zh':'标题','topics':['供应链管理与韧性']},
               {'id':2,'decision':'exclude','reason':'Unrelated','summary':'','evidence':[],'title_zh':'','topics':[]}]
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':items})}]}]}
        result=validate_group(response,rows,partial=True)
        self.assertIn('validation_error',result[1]);self.assertEqual(result[2]['decision'],'exclude')

    def test_parser_reads_publisher_cards_without_browser_or_ai(self):
        p=ListingParser();p.feed('<a href="/journal/x/vol/2/issue/1">Latest issue</a><dl class="js-article article-content"><span class="js-article-subtype">Research article</span><dt><a class="article-content-title" href="/science/article/pii/S123"><span>A supply chain study</span></a></dt><span class="js-article-item-aip-date">Available online 11 November 2026</span></dl>')
        self.assertEqual(p.items[0]['title'],'A supply chain study')
        self.assertEqual(p.items[0]['type'],'Research article')
        self.assertEqual(p.links[0]['text'],'Latest issue')

    def test_baseline_unknown_dates_are_kept_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            db=connect(Path(folder)/'test.sqlite3')
            base={'journal_seed_issn':'2398-9629','title':'A paper','doi':'10.1000/a'}
            with db:
                ingest(db,{**base,'dates':{'published-online':(today()-timedelta(days=30)).isoformat()}},'fixture')
                unknown=ingest(db,{**base,'doi':'10.1000/b'},'fixture')
            self.assertEqual(establish_baseline(db,(today()-timedelta(days=3)).isoformat()),1)
            self.assertEqual(db.execute('SELECT cohort FROM papers WHERE id=?',(unknown,)).fetchone()[0],'date_unverified')
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],2)
            with self.assertRaises(ValueError):establish_baseline(db,today().isoformat())
            db.close()

    def test_delayed_enrichment_does_not_age_out_monitoring_backlog(self):
        with tempfile.TemporaryDirectory() as folder:
            db=connect(Path(folder)/'test.sqlite3')
            establish_baseline(db,(today()-timedelta(days=60)).isoformat())
            paper={'journal_seed_issn':'2398-9629','title':'Delayed supply chain paper',
                   'doi':'10.1000/late','dates':{'published-online':(today()-timedelta(days=35)).isoformat()}}
            with db:
                pid=ingest(db,paper,'late-index')
                ingest(db,{**paper,'abstract':'A verified abstract.','abstract_source':'publisher'},'late-abstract')
                old=ingest(db,{**paper,'doi':'10.1000/before-start','dates':{
                    'published-online':(today()-timedelta(days=90)).isoformat()}},'old-record')
            self.assertEqual(db.execute('SELECT cohort FROM papers WHERE id=?',(pid,)).fetchone()[0],'monitoring')
            self.assertEqual(db.execute('SELECT cohort FROM papers WHERE id=?',(old,)).fetchone()[0],'baseline')
            db.close()

if __name__=='__main__':unittest.main()
