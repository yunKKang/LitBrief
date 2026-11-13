import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from screen_paper import make_request, parse_response, screen, restore_excerpt

PAPER = {'journal_seed_issn': '0306-2619', 'title': 'Synthetic test paper',
         'abstract': 'Housing retrofit reduces energy demand.', 'abstract_source': 'test fixture'}
CONFIG = {'base_url': 'https://example.invalid/v1', 'api_key': 'test-secret',
          'model': 'gpt-6-luna', 'effort': 'high', 'tier': 'fast', 'max_output_tokens': 8000}
RESULT = {'decision': 'include', 'reason': '涉及住房改造', 'summary': '研究住房改造与能源需求。',
          'evidence': ['Housing retrofit reduces energy demand.']}


def response():
    return {'id': 'test-response', 'status': 'completed', 'model': 'gpt-6-luna',
            'service_tier': 'priority',
            'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150,
                      'input_tokens_details': {'cached_tokens': 20},
                      'output_tokens_details': {'reasoning_tokens': 30}},
            'output': [{'type': 'reasoning'}, {'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(RESULT)}]}]}


class ScreeningTests(unittest.TestCase):
    def test_hyphen_restoration_keeps_meaning_and_requires_unique_source(self):
        self.assertEqual(restore_excerpt('The in-use stock','The in\u2010use stock grew.'),'The in\u2010use stock')
        self.assertEqual(restore_excerpt('-20%','\u221220%'),'-20%')
        self.assertEqual(restore_excerpt('in-use','in\u2010use and in\u2011use'),'in-use')
        self.assertEqual(restore_excerpt('stocks grew 20%','stocks grew 10%'),'stocks grew 20%')


    def test_quote_initial_capitalization_restores_exact_source(self):
        good=response()
        good['output'][1]['content'][0]['text']=json.dumps({**RESULT,'evidence':['Reduces energy demand.']})
        result=parse_response(good,PAPER['abstract'])
        self.assertEqual(result['evidence'],['reduces energy demand.'])
        good['output'][1]['content'][0]['text']=json.dumps({**RESULT,'evidence':['Reduces energy consumption.']})
        with self.assertRaises(ValueError):parse_response(good,PAPER['abstract'])
    def test_multiple_exact_excerpts_are_validated_individually(self):
        good = response()
        good['output'][1]['content'][0]['text'] = json.dumps({**RESULT, 'evidence': ['Housing retrofit', 'reduces energy demand.']})
        self.assertEqual(len(parse_response(good, PAPER['abstract'])['evidence']), 2)
        good['output'][1]['content'][0]['text'] = json.dumps({**RESULT, 'evidence': ['Housing retrofit', 'fabricated']})
        with self.assertRaises(ValueError):
            parse_response(good, PAPER['abstract'])
    def test_high_fast_responses_payload(self):
        payload, _ = make_request(PAPER, CONFIG)
        self.assertEqual(payload['reasoning'], {'effort': 'high'})
        self.assertEqual(payload['service_tier'], 'fast')
        self.assertFalse(payload['store'])
        self.assertNotIn('test-secret', json.dumps(payload))

    def test_missing_abstract_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            result = screen({**PAPER, 'abstract': ''}, CONFIG, Path(folder),
                            lambda *_: self.fail('No call allowed'))
            self.assertEqual(result['state'], 'awaiting_abstract')

    def test_excluded_journal_rejected(self):
        with self.assertRaises(ValueError):
            make_request({**PAPER, 'journal_seed_issn': 'excluded'}, CONFIG)

    def test_usage_and_reuse_and_list_tier(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            result = screen(PAPER, CONFIG, output, lambda *_: response())
            self.assertEqual(result['display'], '相关文献列表')
            self.assertEqual(result['state'], 'awaiting_review')
            self.assertFalse(result['published'])
            self.assertEqual(screen(PAPER, CONFIG, output, lambda *_: self.fail('Cached')), result)
            events = (output / 'usage.jsonl').read_text().splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0])
            self.assertEqual(event['usage']['output_tokens_details']['reasoning_tokens'], 30)
            self.assertEqual(event['returned_tier'], 'priority')
            self.assertNotIn('test-secret', events[0])

    def test_incomplete_billed_response_stays_pending_and_records_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with self.assertRaises(ValueError):
                screen(PAPER, CONFIG, output, lambda *_: {**response(), 'status': 'incomplete'})
            self.assertEqual(len(list(output.glob('*.response.json'))), 1)
            self.assertEqual(len(list(output.glob('*.json'))), 1)
            event = json.loads((output / 'usage.jsonl').read_text())
            self.assertEqual(event['usage']['total_tokens'], 150)
            self.assertEqual(event['state'], 'failed')

    def test_unsupported_fast_no_silent_retry(self):
        calls = []
        def fail(*_):
            calls.append(1)
            raise RuntimeError('Provider HTTP 400')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                screen(PAPER, CONFIG, Path(folder), fail)
            self.assertEqual(len(calls), 1)
            event = json.loads((Path(folder) / 'usage.jsonl').read_text())
            self.assertIsNone(event['usage'])

    def test_fabricated_evidence_and_refusal_rejected(self):
        bad = copy.deepcopy(response())
        bad['output'][1]['content'][0]['text'] = json.dumps({**RESULT, 'evidence': ['Invented finding']})
        with self.assertRaises(ValueError):
            parse_response(bad, PAPER['abstract'])
        bad['output'][1]['content'] = [{'type': 'refusal'}]
        with self.assertRaises(ValueError):
            parse_response(bad, PAPER['abstract'])


if __name__ == '__main__':
    unittest.main()
