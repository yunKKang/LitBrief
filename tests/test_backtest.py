import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import backtest


class BacktestTests(unittest.TestCase):
    def test_explicit_isolation_keeps_unknown_cost_and_stops_on_next_failure(self):
        pending={'runnable':1,'fingerprint':'before','states':{'pending':1},'eligible':0,'gates':[]}
        after={**pending,'fingerprint':'after'}
        account={'total':50,'unknown':1,'unknown_request_ids':['first']}
        def completed(*args,**kwargs):
            account.update(unknown=2,unknown_request_ids=['first','second'])
            return {'cycle_id':'fixture','execution_status':'completed'}
        db=Mock();db.execute.return_value.fetchone.return_value={'status':'billing_unknown'}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'progress.json').write_text(json.dumps({'month':'2026-10','initial_usage':0,
                'initial_unknown':0,'windows':[{'kind':'weekly','start':'2026-10-01','end':'2026-10-03'}],'runs':[]}))
            with patch.object(backtest,'usage',side_effect=lambda db:dict(account)), \
                 patch.object(backtest,'state',side_effect=[pending,after,after]), \
                 patch.object(backtest,'cycle',side_effect=completed) as cycle:
                result=backtest.evaluate(db,'2026-10',1000,folder,['first'])
        self.assertEqual(cycle.call_count,1)
        self.assertEqual(result['status'],'billing_needs_attention')
        self.assertEqual(result['unknown_calls_added'],2)
        self.assertEqual(result['accepted_unknown_still_unpriced'],1)
        self.assertEqual(result['spent_tokens'],50)
        self.assertEqual(result['accepted_unknown_requests'],['first'])

    def test_resume_revisits_finished_window_but_does_not_repay_completed_work(self):
        window={'kind':'weekly','start':'2026-10-01','end':'2026-10-03','finished':True}
        pending={'runnable':1,'fingerprint':'before','states':{'awaiting_review':1},'eligible':0,'gates':[]}
        complete={**pending,'runnable':0,'fingerprint':'after','eligible':1}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'progress.json').write_text(json.dumps({'month':'2026-10','initial_usage':100,
                'initial_unknown':1,'windows':[window],'runs':[]}))
            with patch.object(backtest,'usage',return_value={'total':150,'unknown':1}), \
                 patch.object(backtest,'state',side_effect=[pending,complete,complete]), \
                 patch.object(backtest,'cycle',return_value={'cycle_id':'fixture','execution_status':'completed'}) as cycle:
                result=backtest.evaluate(None,'2026-10',1000,folder)
            self.assertEqual(cycle.call_count,1)
            self.assertFalse(cycle.call_args.kwargs.get('publish',False))
            self.assertEqual(result['spent_tokens'],50)
            self.assertEqual(result['status'],'evaluated_available_material')
            with patch.object(backtest,'usage',return_value={'total':150,'unknown':1}), \
                 patch.object(backtest,'state',return_value=complete),patch.object(backtest,'cycle') as cycle:
                backtest.evaluate(None,'2026-10',1000,folder)
            cycle.assert_not_called()

    def test_no_progress_keeps_unfinished_work_visible_and_continues_other_windows(self):
        stuck={'runnable':1,'fingerprint':'same','states':{'pending':1},'eligible':0,'gates':[]}
        done={**stuck,'runnable':0,'states':{}}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(backtest,'usage',return_value={'total':0,'unknown':0}), \
             patch.object(backtest,'state',side_effect=[stuck,stuck,done,done,done,done,done]), \
             patch.object(backtest,'cycle',return_value={'cycle_id':'fixture','execution_status':'completed'}) as cycle:
            result=backtest.evaluate(None,'2026-10',1000,Path(tmp))
        self.assertEqual(cycle.call_count,1)
        self.assertEqual(result['status'],'evaluated_with_blocked_work')
        self.assertFalse(result['windows'][0]['finished'])
        self.assertTrue(all(w['finished'] for w in result['windows'][1:]))


if __name__=='__main__':unittest.main()
