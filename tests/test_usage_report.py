import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from library import connect
from usage_report import report


class UsageTests(unittest.TestCase):
    def test_ledger_log_dedup_cache_and_unknown_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=connect(root/'test.sqlite3')
            usage={'input_tokens':100,'output_tokens':50,'total_tokens':150,
                   'input_tokens_details':{'cached_tokens':40},'output_tokens_details':{'reasoning_tokens':30}}
            for name,detail in [('paid',{}),('cached',{'cache_reused':True})]:
                with db:db.execute('INSERT INTO ai_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (name,name,'titles','[]','{}','unused','applied',json.dumps(usage),json.dumps(detail),'t','t'))
            log=root/'usage.jsonl';log.write_text('\n'.join(json.dumps(r) for r in [
                {'request_id':'paid','usage':usage},{'request_id':'cached','usage':usage},
                {'usage':usage},{'state':'failed','usage':None}]))
            result=report(db,log);db.close()
            self.assertEqual(result['total'],300)
            self.assertEqual(result['reasoning'],60)
            self.assertEqual(result['cached'],80)
            self.assertEqual(result['unknown'],1)
            self.assertEqual(result['cache_reuses'],1)


if __name__=='__main__':unittest.main()
