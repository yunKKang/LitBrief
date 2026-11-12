"""Calendar-scoped historical processing and monthly publication stay isolated."""
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import automation
from library import connect,ingest,review,TOPICS
from source_coverage import summarize
from test_workflow import PAPER


class PeriodScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=connect(self.root/'db.sqlite3')
        self.guard=patch.object(automation,'DATA',self.root);self.guard.start()

    def tearDown(self):
        self.guard.stop();self.db.close();self.tmp.cleanup()

    def paper(self,name,day,seed='2398-9629'):
        with self.db:
            pid=ingest(self.db,{**PAPER,'journal_seed_issn':seed,'doi':'10.1000/'+name,
                'urls':['https://example.org/'+name],'dates':{'published-online':day}},'fixture')
            self.db.execute("UPDATE papers SET cohort='baseline' WHERE id=?",(pid,))
        review(self.db,pid,'approve','Supported summary',[TOPICS[4]],'fixture',1)
        return pid

    def run_window(self,start,end,kind='weekly'):
        return automation.cycle(self.db,publish=True,day=date(2026,11,12),window=(start,end),kind=kind,
              cohort='baseline',output=self.root/'public',runtime={'publication_mode':'automatic'})

    def test_history_and_monthly_are_separate_and_idempotent(self):
        weekly=self.paper('weekly','2026-10-15')
        monthly=self.paper('monthly','2026-10-15','0306-2619')
        outside=self.paper('outside','2026-10-25')
        self.run_window('2026-10-11','2026-10-17')
        self.assertEqual([r[0] for r in self.db.execute('SELECT paper_id FROM issue_items')],[weekly])
        result=self.run_window('2026-10-01','2026-10-31','monthly')
        self.assertEqual(self.db.execute("SELECT issue_id FROM issue_items WHERE paper_id=?",(monthly,)).fetchone()[0],'2026-10-monthly')
        self.assertIsNone(self.db.execute('SELECT 1 FROM issue_items WHERE paper_id=?',(outside,)).fetchone())
        self.assertTrue((Path(result['publication']['public_directory'])/'issues/2026-10-monthly.html').is_file())
        self.run_window('2026-10-01','2026-10-31','monthly')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0],2)

    def test_empty_scope_does_not_publish_other_approved_papers(self):
        self.paper('outside','2026-10-25')
        self.run_window('2026-10-01','2026-10-03')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issues').fetchone()[0],0)
        with self.assertRaises(ValueError):self.run_window('2026-10-01','2026-10-04')
        with self.assertRaises(ValueError):self.run_window('2026-10-02','2026-10-31','monthly')

    def test_coverage_does_not_equate_http_success_or_crossref_to_complete(self):
        folder=self.root/'collection';(folder/'publisher').mkdir(parents=True)
        seed='2398-9629'
        (folder/'publisher'/f'{seed}.json').write_text(json.dumps({'sources':[{'request':{'ok':True,'status':200},'parsed':None}]}))
        row=summarize(self.db,folder,'2026-10-01','2026-10-31',[seed])[0]
        self.assertEqual(row['coverage'],'unknown');self.assertEqual(row['feeds'][0]['status'],'unrecognized_content')
        (folder/'2026/catalogue').mkdir(parents=True)
        (folder/'2026/catalogue'/f'{seed}.json').write_text(json.dumps({'range_traversed':True,'items':[{'doi':'10.1000/missing'}]}))
        row=summarize(self.db,folder,'2026-10-01','2026-10-31',[seed])[0]
        self.assertEqual(row['coverage'],'partial');self.assertEqual(row['missing_dois'],['10.1000/missing'])


if __name__=='__main__':unittest.main()
