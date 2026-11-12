"""Verify publisher identity and bounded HTTP using synthetic responses only."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_sources as audit


class CollectionGuardTests(unittest.TestCase):
    def test_wrong_journal_feed_is_not_importable(self):
        body = b'<rss><channel><title>Wrong journal</title><item><title>Study</title><link>https://doi.org/10.1000/example</link></item></channel></rss>'
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(audit, 'OUTPUT', Path(folder)), patch.object(audit.HTTP, 'get',
                    return_value=({'ok': True, 'status': 200}, body)), redirect_stdout(StringIO()):
                audit.run_publisher(SimpleNamespace(only=['0013-936X'], retry_failed=False))
            result = json.loads((Path(folder) / 'publisher/0013-936X.json').read_text('utf-8'))
            self.assertEqual(len(result['sources']), 2)
            for source in result['sources']:
                self.assertIsNone(source['parsed'])
                self.assertEqual(source['parse_error'], 'Feed journal identity mismatch')

    def test_single_attempt_retains_case_insensitive_retry_after(self):
        with tempfile.TemporaryDirectory() as folder:
            error = HTTPError('https://example.org/feed', 429, 'limited', {'retry-after': '7200'}, BytesIO(b'limited'))
            with patch.object(audit, 'urlopen', side_effect=error) as send, patch.object(audit.time, 'sleep') as sleep:
                meta, _ = audit.HTTP(Path(folder), max_attempts=1, interval_seconds=2).get(error.url)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(meta['retry_after'], '7200')
            self.assertEqual(meta['attempts'][0]['retry_after'], '7200')
            self.assertFalse(meta['ok'])
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
